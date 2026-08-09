import os
import logging
import re
import threading
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Union

from core.downloader import (
    download_video,
    download_audio,
    download_audio_native,
    download_thumbnail,
    download_tiktok_media,
    DownloadCancelledException,
)
from core.extractor import is_tiktok_url

logger = logging.getLogger(__name__)

# Valid task statuses matching TaskStatus contract
# "QUEUED" | "FETCHING_INFO" | "DOWNLOADING" | "MERGING" | "DONE" | "ERROR" | "CANCELLING" | "CANCELLED"


class TaskQueue:
    def __init__(self, max_concurrent: int = 4):
        self.max_concurrent = max_concurrent
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._lock = threading.Lock()
        self._file_lock = threading.Lock()

        self.tasks: Dict[str, dict] = {}  # taskId -> progress info
        self.cancelled_tasks: Set[str] = set()
        self.listeners: Set[Callable[[dict], None]] = set()
        self.default_output_dir = os.path.join(os.path.expanduser("~"), "Downloads", "Origin Downloads")
        self._last_start_time = 0.0
        self._start_lock = threading.Lock()
        self._purge_orphan_tmp_dir(self.default_output_dir)

    def _stagger_start(self, min_delay: float = 0.45):
        """Spaces out concurrent network request starts by at least min_delay seconds to avoid 403 Forbidden rate limits."""
        with self._start_lock:
            now = time.time()
            elapsed = now - self._last_start_time
            if elapsed < min_delay:
                time.sleep(min_delay - elapsed)
            self._last_start_time = time.time()

    def _purge_orphan_tmp_dir(self, output_dir: str):
        try:
            tmp_dir = os.path.join(os.path.abspath(output_dir), ".origin-tmp")
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Could not purge orphan tmp dir: %s", e)

    def register_progress_listener(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self.listeners.add(callback)

        def unsubscribe():
            with self._lock:
                self.listeners.discard(callback)

        return unsubscribe

    def emit_progress(self, progress: dict):
        with self._lock:
            task_id = progress.get("taskId")
            if task_id and task_id in self.tasks:
                self.tasks[task_id].update(progress)
            listeners_copy = list(self.listeners)

        for callback in listeners_copy:
            try:
                callback(progress)
            except Exception as e:
                logger.error(f"Error in progress listener: {e}")

    def has_active_tasks(self) -> bool:
        with self._lock:
            for task in self.tasks.values():
                if task.get("status") in ("QUEUED", "FETCHING_INFO", "DOWNLOADING", "MERGING"):
                    return True
            return False

    def _init_task(self, taskId: str):
        with self._lock:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", taskId):
                raise ValueError("Mã tác vụ không hợp lệ.")
            if taskId in self.tasks:
                raise ValueError("Mã tác vụ đã được sử dụng.")
            self.cancelled_tasks.discard(taskId)
            self.tasks[taskId] = {
                "taskId": taskId,
                "status": "QUEUED",
                "percent": 0.0,
                "speedKBs": 0.0,
                "etaSec": 0,
            }

    def _clear_cancel_state(self, taskId: str):
        with self._lock:
            self.cancelled_tasks.discard(taskId)

    def add_video_task(
        self,
        taskId: str,
        url: str,
        height: int,
        output_dir: Optional[str] = None,
        output_format: str = "best",
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
        sub_lang: Optional[str] = None,
        embed_sub: bool = False,
    ):
        target_dir = output_dir or self.default_output_dir
        self._init_task(taskId)
        self.emit_progress({"taskId": taskId, "status": "QUEUED", "percent": 0.0})
        self.executor.submit(
            self._run_video_task,
            taskId,
            url,
            height,
            target_dir,
            output_format,
            start_sec,
            end_sec,
            sub_lang,
            embed_sub,
        )

    def add_audio_task(
        self,
        taskId: str,
        url: str,
        bitrate: Union[int, str],
        output_dir: Optional[str] = None,
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
    ):
        target_dir = output_dir or self.default_output_dir
        self._init_task(taskId)
        self.emit_progress({"taskId": taskId, "status": "QUEUED", "percent": 0.0})
        self.executor.submit(
            self._run_audio_task, taskId, url, bitrate, target_dir, start_sec, end_sec
        )

    def add_audio_native_task(
        self,
        taskId: str,
        url: str,
        output_dir: Optional[str] = None,
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
    ):
        target_dir = output_dir or self.default_output_dir
        self._init_task(taskId)
        self.emit_progress({"taskId": taskId, "status": "QUEUED", "percent": 0.0})
        self.executor.submit(
            self._run_audio_native_task, taskId, url, target_dir, start_sec, end_sec
        )

    def add_thumbnail_task(self, taskId: str, url: str, output_dir: Optional[str] = None):
        target_dir = output_dir or self.default_output_dir
        self._init_task(taskId)
        self.emit_progress({"taskId": taskId, "status": "QUEUED", "percent": 0.0})
        self.executor.submit(self._run_thumbnail_task, taskId, url, target_dir)

    def cancel_task(self, taskId: str):
        """Request cancellation.  Emits ``CANCELLING``; the worker emits ``CANCELLED``
        once cleanup is complete."""
        with self._lock:
            self.cancelled_tasks.add(taskId)
            task_info = self.tasks.get(taskId)

        if task_info:
            current_status = task_info.get("status", "")
            # Already terminal — nothing to do
            if current_status in ("DONE", "ERROR", "CANCELLED"):
                return
            self.emit_progress(
                {
                    "taskId": taskId,
                    "status": "CANCELLING",
                    "percent": task_info.get("percent", 0.0),
                }
            )

    def is_cancelled(self, taskId: str) -> bool:
        with self._lock:
            return taskId in self.cancelled_tasks

    def _work_dir(self, output_dir: str, task_id: str) -> str:
        return os.path.join(os.path.abspath(output_dir), ".origin-tmp", task_id)

    def _cleanup_work_dir(self, work_dir: str):
        try:
            if os.path.exists(work_dir):
                for _ in range(10):
                    try:
                        shutil.rmtree(work_dir)
                        break
                    except OSError:
                        time.sleep(0.02)
            parent = os.path.dirname(work_dir)
            if os.path.isdir(parent):
                for _ in range(10):
                    try:
                        if os.path.isdir(parent) and not os.listdir(parent):
                            os.rmdir(parent)
                            break
                    except OSError:
                        time.sleep(0.02)
        except OSError as error:
            logger.warning("Could not clean task workspace %s: %s", work_dir, error)

    def _move_completed_files(self, work_dir: str, output_dir: str):
        work_path = Path(work_dir)
        if not work_path.exists():
            return
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for source in work_path.iterdir():
            if not source.is_file() or source.suffix in {".part", ".ytdl"}:
                continue
            with self._file_lock:
                target = destination / source.name
                suffix = 1
                while target.exists():
                    target = destination / f"{source.stem} ({suffix}){source.suffix}"
                    suffix += 1
                target.touch(exist_ok=False)
            shutil.move(str(source), str(target))

    def _run_video_task(
        self,
        taskId: str,
        url: str,
        height: int,
        output_dir: str,
        output_format: str,
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
        sub_lang: Optional[str] = None,
        embed_sub: bool = False,
    ):
        work_dir = self._work_dir(output_dir, taskId)
        if self.is_cancelled(taskId):
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)
            return

        self.emit_progress({"taskId": taskId, "status": "FETCHING_INFO", "percent": 0.0})

        def progress_cb(p: dict):
            if self.is_cancelled(taskId):
                raise DownloadCancelledException("Task cancelled by user.")
            p["taskId"] = taskId
            self.emit_progress(p)

        try:
            self._stagger_start()
            if is_tiktok_url(url):
                download_tiktok_media(
                    url=url,
                    output_dir=work_dir,
                    mode="video",
                    height=height,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )
            else:
                download_video(
                    url=url,
                    height=height,
                    output_dir=work_dir,
                    output_format=output_format,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    sub_lang=sub_lang,
                    embed_sub=embed_sub,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )

            if self.is_cancelled(taskId):
                self._cleanup_work_dir(work_dir)
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
                return

            self._move_completed_files(work_dir, output_dir)
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "DONE", "percent": 100.0})

        except DownloadCancelledException:
            logger.info(f"Task {taskId} download cancelled.")
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
        except Exception as e:
            logger.error(f"Task {taskId} failed: {e}")
            with self._lock:
                current_pct = self.tasks.get(taskId, {}).get("percent", 0.0)
            self.emit_progress(
                {
                    "taskId": taskId,
                    "status": "ERROR",
                    "percent": current_pct,
                    "errorMessage": str(e),
                }
            )
        finally:
            self._cleanup_work_dir(work_dir)
            if self.is_cancelled(taskId) and self.tasks.get(taskId, {}).get("status") != "CANCELLED":
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)

    def _run_audio_task(
        self,
        taskId: str,
        url: str,
        bitrate: Union[int, str],
        output_dir: str,
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
    ):
        work_dir = self._work_dir(output_dir, taskId)
        if self.is_cancelled(taskId):
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)
            return

        self.emit_progress({"taskId": taskId, "status": "FETCHING_INFO", "percent": 0.0})

        def progress_cb(p: dict):
            if self.is_cancelled(taskId):
                raise DownloadCancelledException("Task cancelled by user.")
            p["taskId"] = taskId
            self.emit_progress(p)

        try:
            self._stagger_start()
            if is_tiktok_url(url):
                download_tiktok_media(
                    url=url,
                    output_dir=work_dir,
                    mode="audio",
                    bitrate=bitrate,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )
            else:
                download_audio(
                    url=url,
                    bitrate_kbps=bitrate,
                    output_dir=work_dir,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )

            if self.is_cancelled(taskId):
                return

            self._move_completed_files(work_dir, output_dir)
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "DONE", "percent": 100.0})

        except DownloadCancelledException:
            logger.info(f"Task {taskId} download cancelled.")
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
        except Exception as e:
            logger.error(f"Task {taskId} failed: {e}")
            with self._lock:
                current_pct = self.tasks.get(taskId, {}).get("percent", 0.0)
            self.emit_progress(
                {
                    "taskId": taskId,
                    "status": "ERROR",
                    "percent": current_pct,
                    "errorMessage": str(e),
                }
            )
        finally:
            self._cleanup_work_dir(work_dir)
            if self.is_cancelled(taskId) and self.tasks.get(taskId, {}).get("status") != "CANCELLED":
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)

    def _run_audio_native_task(
        self,
        taskId: str,
        url: str,
        output_dir: str,
        start_sec: Optional[int] = None,
        end_sec: Optional[int] = None,
    ):
        work_dir = self._work_dir(output_dir, taskId)
        if self.is_cancelled(taskId):
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)
            return

        self.emit_progress({"taskId": taskId, "status": "FETCHING_INFO", "percent": 0.0})

        def progress_cb(p: dict):
            if self.is_cancelled(taskId):
                raise DownloadCancelledException("Task cancelled by user.")
            p["taskId"] = taskId
            self.emit_progress(p)

        try:
            self._stagger_start()
            if is_tiktok_url(url):
                download_tiktok_media(
                    url=url,
                    output_dir=work_dir,
                    mode="audio_native",
                    start_sec=start_sec,
                    end_sec=end_sec,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )
            else:
                download_audio_native(
                    url=url,
                    output_dir=work_dir,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )

            if self.is_cancelled(taskId):
                return

            self._move_completed_files(work_dir, output_dir)
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "DONE", "percent": 100.0})

        except DownloadCancelledException:
            logger.info(f"Task {taskId} download cancelled.")
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
        except Exception as e:
            logger.error(f"Task {taskId} failed: {e}")
            with self._lock:
                current_pct = self.tasks.get(taskId, {}).get("percent", 0.0)
            self.emit_progress(
                {
                    "taskId": taskId,
                    "status": "ERROR",
                    "percent": current_pct,
                    "errorMessage": str(e),
                }
            )
        finally:
            self._cleanup_work_dir(work_dir)
            if self.is_cancelled(taskId) and self.tasks.get(taskId, {}).get("status") != "CANCELLED":
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)

    def _run_thumbnail_task(self, taskId: str, url: str, output_dir: str):
        work_dir = self._work_dir(output_dir, taskId)
        if self.is_cancelled(taskId):
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)
            return

        self.emit_progress({"taskId": taskId, "status": "FETCHING_INFO", "percent": 0.0})

        def progress_cb(p: dict):
            if self.is_cancelled(taskId):
                raise DownloadCancelledException("Task cancelled by user.")
            p["taskId"] = taskId
            self.emit_progress(p)

        try:
            self._stagger_start()
            if is_tiktok_url(url):
                download_tiktok_media(
                    url=url,
                    output_dir=work_dir,
                    mode="photo",
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )
            else:
                download_thumbnail(
                    url=url,
                    output_dir=work_dir,
                    progress_callback=progress_cb,
                    is_cancelled_check=lambda: self.is_cancelled(taskId),
                )

            if self.is_cancelled(taskId):
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
                return

            self._move_completed_files(work_dir, output_dir)
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "DONE", "percent": 100.0})

        except DownloadCancelledException:
            logger.info(f"Task {taskId} download cancelled.")
            self._cleanup_work_dir(work_dir)
            self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
        except Exception as e:
            logger.error(f"Task {taskId} failed: {e}")
            with self._lock:
                current_pct = self.tasks.get(taskId, {}).get("percent", 0.0)
            self.emit_progress(
                {
                    "taskId": taskId,
                    "status": "ERROR",
                    "percent": current_pct,
                    "errorMessage": str(e),
                }
            )
        finally:
            self._cleanup_work_dir(work_dir)
            if self.is_cancelled(taskId):
                self.emit_progress({"taskId": taskId, "status": "CANCELLED", "percent": 0.0})
            self._clear_cancel_state(taskId)

    def clear_completed_tasks(self):
        with self._lock:
            to_remove = [
                tid for tid, task in self.tasks.items()
                if task.get("status") in {"DONE", "ERROR", "CANCELLED"}
            ]
            for tid in to_remove:
                del self.tasks[tid]

    def shutdown(self, timeout: float = 10.0):
        """Non-blocking bounded shutdown.

        Cancels every in-flight task, then spawns a daemon thread that waits up
        to *timeout* seconds for workers to drain.  The Qt GUI thread is never
        blocked.
        """
        logger.info("Shutting down task queue (timeout=%.1fs)...", timeout)
        with self._lock:
            self.cancelled_tasks.update(
                task_id
                for task_id, task in self.tasks.items()
                if task.get("status") not in {"DONE", "ERROR", "CANCELLED"}
            )

        def _drain():
            self.executor.shutdown(wait=True, cancel_futures=True)
            self._purge_orphan_tmp_dir(self.default_output_dir)
            logger.info("Task queue shutdown complete.")

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            logger.warning(
                "Task queue shutdown timed out after %.1fs — worker threads may "
                "still be running in the background.",
                timeout,
            )
