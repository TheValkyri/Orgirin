import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from core import downloader, extractor
from core.downloader import DownloadCancelledException
from core.task_queue import TaskQueue
from shell.qt_bridge import QtBridge


def wait_for(queue, task_id, statuses=("DONE", "ERROR", "CANCELLED"), timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = queue.tasks.get(task_id, {}).get("status")
        if status in statuses:
            return status
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish: {queue.tasks.get(task_id)}")


class FakeYDL:
    instances = []

    def __init__(self, options):
        self.options = options
        self.downloaded = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def download(self, urls):
        self.downloaded = urls
        for hook in self.options.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
        for hook in self.options.get("postprocessor_hooks", []):
            hook({"status": "started"})


class CoreTests(unittest.TestCase):
    def test_video_prefers_mp4_and_reports_merging(self):
        events = []
        FakeYDL.instances.clear()
        with patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL):
            downloader.download_video("https://example.test/video", 1080, tempfile.mkdtemp(), progress_callback=events.append)

        options = FakeYDL.instances[0].options
        self.assertTrue(options["format"].startswith("bestvideo[height<=1080]+bestaudio"))
        self.assertEqual(options["concurrent_fragment_downloads"], 4)
        self.assertEqual(events[-1]["status"], "MERGING")

    def test_playlist_without_detected_formats_does_not_invent_qualities(self):
        playlist = {
            "_type": "playlist",
            "title": "Playlist",
            "entries": [{"id": "abc123", "title": "Video", "url": "https://example.test/video"}],
        }
        video = {"duration": 5, "formats": []}

        class ExtractYDL(FakeYDL):
            calls = 0

            def extract_info(self, *_args, **_kwargs):
                self.__class__.calls += 1
                return playlist if self.__class__.calls == 1 else video

        ExtractYDL.calls = 0
        with patch.object(extractor.yt_dlp, "YoutubeDL", ExtractYDL):
            info = extractor.extract_video_info("https://example.test/playlist")

        self.assertTrue(info["isPlaylist"])
        self.assertEqual(info["formats"], [])

    def test_task_workspace_isolated_and_duplicate_names_are_preserved(self):
        def fake_download(**kwargs):
            Path(kwargs["output_dir"]).mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_dir"], "sample.mp4").write_bytes(b"video")
            kwargs["progress_callback"]({"status": "DOWNLOADING", "percent": 50})

        with tempfile.TemporaryDirectory() as output_dir, patch("core.task_queue.download_video", fake_download):
            queue = TaskQueue(max_concurrent=2)
            try:
                queue.add_video_task("task-one", "https://example.test/one", 720, output_dir)
                queue.add_video_task("task-two", "https://example.test/two", 720, output_dir)
                self.assertEqual(wait_for(queue, "task-one"), "DONE")
                self.assertEqual(wait_for(queue, "task-two"), "DONE")
                self.assertEqual(sorted(path.name for path in Path(output_dir).glob("*.mp4")), ["sample (1).mp4", "sample.mp4"])
                self.assertFalse(Path(output_dir, ".origin-tmp").exists())
            finally:
                queue.shutdown()

    def test_cancelling_task_only_removes_its_workspace(self):
        def cancelled_download(**kwargs):
            Path(kwargs["output_dir"]).mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_dir"], "partial.mp4.part").write_bytes(b"partial")
            raise DownloadCancelledException()

        with tempfile.TemporaryDirectory() as output_dir, patch("core.task_queue.download_video", cancelled_download):
            unrelated = Path(output_dir, "another-task.part")
            unrelated.write_bytes(b"keep")
            queue = TaskQueue()
            try:
                queue.add_video_task("task-cancel", "https://example.test/video", 720, output_dir)
                self.assertEqual(wait_for(queue, "task-cancel"), "CANCELLED")
                self.assertTrue(unrelated.exists())
                self.assertFalse(Path(output_dir, ".origin-tmp").exists())
            finally:
                queue.shutdown()

    def test_cancelled_queued_task_releases_its_cancel_state(self):
        started = threading.Event()
        release = threading.Event()

        def blocked_download(**kwargs):
            Path(kwargs["output_dir"]).mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_dir"], "running.mp4").write_bytes(b"video")
            started.set()
            release.wait(1)

        with tempfile.TemporaryDirectory() as output_dir, patch("core.task_queue.download_video", blocked_download):
            queue = TaskQueue(max_concurrent=1)
            try:
                queue.add_video_task("running", "https://example.test/one", 720, output_dir)
                self.assertTrue(started.wait(1))
                queue.add_video_task("queued", "https://example.test/two", 720, output_dir)
                queue.cancel_task("queued")
                release.set()
                self.assertEqual(wait_for(queue, "queued"), "CANCELLED")
                deadline = time.monotonic() + 1
                while "queued" in queue.cancelled_tasks and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertNotIn("queued", queue.cancelled_tasks)
            finally:
                release.set()
                queue.shutdown()

    def test_task_ids_cannot_escape_or_be_reused(self):
        queue = TaskQueue()
        try:
            with self.assertRaises(ValueError):
                queue.add_video_task("../escape", "https://example.test/video", 720)
            queue._init_task("unique-task")
            with self.assertRaises(ValueError):
                queue._init_task("unique-task")
        finally:
            queue.shutdown()


class QtBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_fetch_video_info_async_returns_without_blocking_qt_thread(self):
        queue = TaskQueue()
        bridge = QtBridge(queue)
        received = []
        bridge.onVideoInfoSignal.connect(received.append)
        try:
            with patch("shell.qt_bridge.extract_video_info", return_value={"title": "Async video"}):
                request_id = bridge.fetchVideoInfoAsync("https://example.test/video")
                deadline = time.monotonic() + 1
                while not received and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
            self.assertTrue(received)
            result = json.loads(received[0])
            self.assertEqual(result["requestId"], request_id)
            self.assertEqual(result["payload"]["title"], "Async video")
        finally:
            bridge.shutdown()
            queue.shutdown()


if __name__ == "__main__":
    unittest.main()
