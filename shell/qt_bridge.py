import os
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Slot, Signal, QMetaObject, Qt, Q_ARG
from PySide6.QtWidgets import QFileDialog, QApplication

from core.extractor import extract_video_info
from core.task_queue import TaskQueue

logger = logging.getLogger(__name__)

def _redact_url(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url.strip())
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url.split("?")[0]


class QtBridge(QObject):
    # Signal emitted to JavaScript when task progress changes
    onProgressSignal = Signal(str)
    onVideoInfoSignal = Signal(str)
    _internalProgressSignal = Signal(str)
    _internalVideoInfoSignal = Signal(str, str)

    def __init__(self, task_queue: TaskQueue, parent=None):
        super().__init__(parent)
        self.task_queue = task_queue
        self._info_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="origin-info")
        self._internalProgressSignal.connect(self._emit_progress)
        self._internalVideoInfoSignal.connect(self._on_video_info_ready)
        self._unsubscribe_progress = self.task_queue.register_progress_listener(self._on_task_progress)

    def _on_task_progress(self, progress_dict: dict):
        try:
            payload = json.dumps(progress_dict)
            self._internalProgressSignal.emit(payload)
        except Exception as e:
            logger.error(f"Failed to serialize progress dict: {e}")

    @Slot(str)
    def _emit_progress(self, payload_str: str):
        self.onProgressSignal.emit(payload_str)
        try:
            parent_window = self.parent()
            if parent_window and hasattr(parent_window, "web_page"):
                clean_payload = payload_str.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
                js = f"window.__onTaskProgress && window.__onTaskProgress({clean_payload});"
                parent_window.web_page.runJavaScript(js)
        except Exception as e:
            logger.error(f"Failed to runJavaScript for progress: {e}")

    @Slot(str, result=str)
    def fetchVideoInfoAsync(self, url: str) -> str:
        request_id = f"info_{uuid.uuid4().hex}"
        self._info_executor.submit(self._fetch_video_info_async, request_id, url)
        return request_id

    def _fetch_video_info_async(self, request_id: str, url: str):
        clean_url = _redact_url(url)
        logger.info("[BRIDGE] _fetch_video_info_async START req=%s url=%s", request_id, clean_url)
        try:
            payload = extract_video_info(url)
            if not isinstance(payload, dict):
                raise RuntimeError("Lỗi hệ thống: extract_video_info không trả về dữ liệu hợp lệ.")
            logger.info("[BRIDGE] extract_video_info SUCCESS req=%s title=%s", request_id, payload.get("title"))
        except Exception as error:
            logger.error("[BRIDGE] fetchVideoInfoAsync error for %s: %s", clean_url, error)
            payload = {"error": str(error)}
        payload_str = json.dumps(payload)
        logger.info("[BRIDGE] Emitting _internalVideoInfoSignal req=%s", request_id)
        self._internalVideoInfoSignal.emit(request_id, payload_str)

    @Slot(str, str)
    def _on_video_info_ready(self, request_id: str, payload_json: str):
        logger.info("[BRIDGE] _on_video_info_ready CALLED req=%s", request_id)
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {"error": payload_json}
        signal_payload = json.dumps({"requestId": request_id, "payload": payload})
        logger.info("[BRIDGE] Emitting onVideoInfoSignal req=%s", request_id)
        self.onVideoInfoSignal.emit(signal_payload)
        
        # Dual delivery via direct JS execution to guarantee delivery even if QWebChannel signal drops
        try:
            parent_window = self.parent()
            if parent_window and hasattr(parent_window, "web_page"):
                req_json = json.dumps(request_id)
                data_json = json.dumps(payload).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
                js = f"window.__onVideoInfoReady && window.__onVideoInfoReady({req_json}, {data_json});"
                logger.info("[BRIDGE] Executing runJavaScript for req=%s", request_id)
                parent_window.web_page.runJavaScript(js)
        except Exception as e:
            logger.error(f"Failed runJavaScript in _on_video_info_ready: {e}")
        logger.info("[BRIDGE] onVideoInfoSignal emitted OK req=%s", request_id)

    def shutdown(self):
        self._unsubscribe_progress()
        self._info_executor.shutdown(wait=False, cancel_futures=True)

    @Slot(str, result=str)
    def startVideoDownload(self, params_json: str) -> str:
        # NOTE: never let an exception escape this Slot. When a @Slot invoked
        # through QWebChannel raises, Qt never sends an invokeMethod response
        # back to the page - the JS Promise from bridge.startVideoDownload()
        # is left pending forever, which is exactly why the "Tai xuong" button
        # looked like it did nothing. Always return a JSON string instead.
        try:
            params = json.loads(params_json)
            url = params["url"]
            height = int(params["height"])
            output_dir = params.get("outputDir") or None
            output_format = params.get("outputFormat", "best")
            task_id = params.get("taskId") or f"task_v_{uuid.uuid4().hex[:8]}"
            # Use the taskId generated client-side (bridge.ts) so the UI can
            # register task metadata *before* calling this method - otherwise
            # the first "QUEUED" progress signal (emitted synchronously below)
            # can race ahead of the JS code that records the task's title/label.
            start_sec = params.get("startSec")
            end_sec = params.get("endSec")
            sub_lang = params.get("subLang")
            embed_sub = bool(params.get("embedSub", False))

            self.task_queue.add_video_task(
                taskId=task_id,
                url=url,
                height=height,
                output_dir=output_dir,
                output_format=output_format,
                start_sec=start_sec,
                end_sec=end_sec,
                sub_lang=sub_lang,
                embed_sub=embed_sub,
            )
            return json.dumps({"taskId": task_id})
        except Exception as e:
            logger.error(f"startVideoDownload error: {e}")
            return json.dumps({"error": str(e)})

    @Slot(str, result=str)
    def startAudioDownload(self, params_json: str) -> str:
        try:
            params = json.loads(params_json)
            url = params["url"]
            bitrate = params.get("bitrateKbps", "original")
            if bitrate != "original":
                try:
                    bitrate = int(bitrate)
                except ValueError:
                    bitrate = "original"
            output_dir = params.get("outputDir") or None
            task_id = params.get("taskId") or f"task_a_{uuid.uuid4().hex[:8]}"
            start_sec = params.get("startSec")
            end_sec = params.get("endSec")

            self.task_queue.add_audio_task(
                taskId=task_id,
                url=url,
                bitrate=bitrate,
                output_dir=output_dir,
                start_sec=start_sec,
                end_sec=end_sec,
            )
            return json.dumps({"taskId": task_id})
        except Exception as e:
            logger.error(f"startAudioDownload error: {e}")
            return json.dumps({"error": str(e)})

    @Slot(str, result=str)
    def startAudioNativeDownload(self, params_json: str) -> str:
        try:
            params = json.loads(params_json)
            url = params["url"]
            output_dir = params.get("outputDir") or None
            task_id = params.get("taskId") or f"task_an_{uuid.uuid4().hex[:8]}"
            start_sec = params.get("startSec")
            end_sec = params.get("endSec")

            self.task_queue.add_audio_native_task(
                taskId=task_id,
                url=url,
                output_dir=output_dir,
                start_sec=start_sec,
                end_sec=end_sec,
            )
            return json.dumps({"taskId": task_id})
        except Exception as e:
            logger.error(f"startAudioNativeDownload error: {e}")
            return json.dumps({"error": str(e)})

    @Slot(str, result=str)
    def startThumbnailDownload(self, params_json: str) -> str:
        try:
            params = json.loads(params_json)
            url = params["url"]
            output_dir = params.get("outputDir") or None
            task_id = params.get("taskId") or f"task_thumb_{uuid.uuid4().hex[:8]}"

            self.task_queue.add_thumbnail_task(
                taskId=task_id,
                url=url,
                output_dir=output_dir,
            )
            return json.dumps({"taskId": task_id})
        except Exception as e:
            logger.error(f"startThumbnailDownload error: {e}")
            return json.dumps({"error": str(e)})

    @Slot(str)
    def cancelTask(self, taskId: str):
        try:
            self.task_queue.cancel_task(taskId)
        except Exception as e:
            logger.error(f"cancelTask error for {taskId}: {e}")

    @Slot(result=str)
    def pickOutputDir(self) -> str:
        try:
            top_widget = QApplication.activeWindow()
            dir_path = QFileDialog.getExistingDirectory(
                top_widget,
                "Chọn thư mục lưu video/audio",
                "",
                QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
            )
            return dir_path if dir_path else ""
        except Exception as e:
            logger.error(f"pickOutputDir error: {e}")
            return ""

    @Slot(str)
    def openFolder(self, path_str: str):
        try:
            target_dir = path_str or self.task_queue.default_output_dir
            target_dir = os.path.abspath(target_dir)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            os.startfile(target_dir)
        except Exception as e:
            logger.error(f"openFolder error: {e}")
