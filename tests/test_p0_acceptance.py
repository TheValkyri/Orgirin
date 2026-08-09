import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import downloader, extractor
from core.downloader import (
    FFmpegTools,
    ProbeResult,
    _verify_downloaded_file,
    get_ffmpeg_tools,
    download_tiktok_media,
)
from core.extractor import is_tiktok_url
from core.task_queue import TaskQueue, DownloadCancelledException
from tiktok_extractor.errors import (
    NoCleanGearAvailableError,
    VerificationMismatchError,
)
from tiktok_extractor.models import TikTokMediaGear, TikTokMediaResult
from tiktok_extractor.parser import parse_page_json
from tiktok_extractor.url_resolver import classify_url


class TestP0VerificationFailClosed(unittest.TestCase):
    """P0-1 Acceptance Criteria Tests."""

    def test_ffmpeg_tools_dataclass(self):
        tools = FFmpegTools(ffmpeg_bin="/path/to/ffmpeg.exe", ffprobe_bin=None)
        self.assertEqual(tools.ffmpeg_bin, "/path/to/ffmpeg.exe")
        self.assertIsNone(tools.ffprobe_bin)
        self.assertEqual(tools.ffmpeg_dir, "/path/to")

    def test_get_ffmpeg_tools_discovery(self):
        tools = get_ffmpeg_tools()
        self.assertIsInstance(tools, FFmpegTools)

    def test_verify_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "missing.mp4"
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "video")
            self.assertIn("không tồn tại", str(ctx.exception))

    def test_verify_small_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "tiny.mp4"
            file_path.write_bytes(b"12345")
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "video")
            self.assertIn("kích thước quá nhỏ", str(ctx.exception))

    def test_verify_photo_bad_header_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_photo.jpg"
            file_path.write_bytes(b"NOT_AN_IMAGE_HEADER_1234567890" * 50)
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "photo")
            self.assertIn("header không hợp lệ", str(ctx.exception))

    def test_verify_photo_valid_header_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "good_photo.jpg"
            file_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 2000)
            res = _verify_downloaded_file(file_path, "photo")
            self.assertIsNone(res)

    def test_verify_missing_ffprobe_raises(self):
        with tempfile.TemporaryDirectory() as tmp, patch("core.downloader.get_ffmpeg_tools", return_value=FFmpegTools()):
            file_path = Path(tmp) / "video.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "video")
            self.assertIn("Thiếu ffprobe", str(ctx.exception))

    def test_verify_ffprobe_non_zero_exit_raises(self):
        tools = get_ffmpeg_tools()
        if not tools.ffprobe_bin:
            self.skipTest("ffprobe executable not available on path for mock test")

        mock_sub = MagicMock(returncode=1, stderr="Corrupted file format")
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", return_value=mock_sub):
            file_path = Path(tmp) / "corrupt.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "video")
            self.assertIn("mã lỗi", str(ctx.exception))

    def test_verify_video_resolution_mismatch_raises(self):
        tools = get_ffmpeg_tools()
        if not tools.ffprobe_bin:
            self.skipTest("ffprobe executable not available")

        # Mock ffprobe returning 360p video
        ffprobe_json = json.dumps({
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360}],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "10.0"}
        })
        mock_sub = MagicMock(returncode=0, stdout=ffprobe_json)
        expected_gear = TikTokMediaGear(url="http://test", width=1920, height=1080, codec="h264", bitrate=1000)

        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", return_value=mock_sub):
            file_path = Path(tmp) / "lowres.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(VerificationMismatchError) as ctx:
                _verify_downloaded_file(file_path, "video", expected_gear=expected_gear)
            self.assertIn("thấp hơn 70%", str(ctx.exception))


class TestP0TikTokTruthfulness(unittest.TestCase):
    """P0-2 Acceptance Criteria Tests."""

    def test_gear_watermark_status_defaults_and_selection(self):
        clean_gear = TikTokMediaGear(url="http://clean", width=1920, height=1080, codec="h264", bitrate=1000, watermark_status="confirmed_clean")
        unknown_gear = TikTokMediaGear(url="http://unknown", width=1920, height=1080, codec="h264", bitrate=1000, watermark_status="unknown")
        wm_gear = TikTokMediaGear(url="http://wm", width=1920, height=1080, codec="h264", bitrate=1000, watermark_status="confirmed_watermarked")

        res_clean = TikTokMediaResult(post_type="video", post_id="1", author_handle="a", caption="", created_at=0, video_gears=[clean_gear, wm_gear])
        self.assertEqual(res_clean.best_clean_gear(), clean_gear)

        res_unknown = TikTokMediaResult(post_type="video", post_id="2", author_handle="a", caption="", created_at=0, video_gears=[unknown_gear, wm_gear])
        self.assertIsNone(res_unknown.best_clean_gear())
        self.assertEqual(res_unknown.best_available_gear(), unknown_gear)

        res_wm_only = TikTokMediaResult(post_type="video", post_id="3", author_handle="a", caption="", created_at=0, video_gears=[wm_gear])
        self.assertIsNone(res_wm_only.best_clean_gear())
        self.assertIsNone(res_wm_only.best_available_gear())

    def test_extractor_info_watermark_labels(self):
        res = TikTokMediaResult(
            post_type="video",
            post_id="123",
            author_handle="testuser",
            caption="test",
            created_at=0,
            video_gears=[
                TikTokMediaGear(url="http://1", width=1920, height=1080, codec="h264", bitrate=1000, watermark_status="confirmed_clean"),
                TikTokMediaGear(url="http://2", width=1280, height=720, codec="h264", bitrate=800, watermark_status="unknown"),
                TikTokMediaGear(url="http://3", width=640, height=360, codec="h264", bitrate=500, watermark_status="confirmed_watermarked"),
            ]
        )
        with patch("tiktok_extractor.orchestrator.ExtractorOrchestrator.extract", return_value=res):
            info = extractor.extract_video_info("https://www.tiktok.com/@testuser/video/123")
            labels = [f["resolutionLabel"] for f in info["formats"]]
            self.assertEqual(len(labels), 2)  # watermarked gear (360p) is filtered out
            self.assertIn("1080p (H.264)", labels[0])
            self.assertIn("720p (H.264)", labels[1])
            for fmt in info["formats"]:
                self.assertEqual(fmt["fps"], 0)
                self.assertEqual(fmt["acodec"], "unknown")


class TestP0QueueWorkspaceURLSafety(unittest.TestCase):
    """P0-3 Acceptance Criteria Tests."""

    def test_strict_url_validation(self):
        self.assertTrue(is_tiktok_url("https://www.tiktok.com/@user/video/123456789"))
        self.assertTrue(is_tiktok_url("https://vt.tiktok.com/ZS12345/"))

        self.assertFalse(is_tiktok_url("https://evil.com/tiktok.com"))
        self.assertFalse(is_tiktok_url("https://user:pass@www.tiktok.com/@user/video/123"))
        self.assertFalse(is_tiktok_url("https://127.0.0.1/video/123"))
        self.assertFalse(is_tiktok_url("https://localhost/video/123"))
        self.assertFalse(is_tiktok_url("ftp://www.tiktok.com/@user/video/123"))

    def test_classify_url_rejects_malicious_inputs(self):
        with self.assertRaises(ValueError):
            classify_url("http://user:pass@www.tiktok.com/@user/video/123")
        with self.assertRaises(ValueError):
            classify_url("https://127.0.0.1/@user/video/123")
        with self.assertRaises(ValueError):
            classify_url("https://192.168.1.1/@user/video/123")
        with self.assertRaises(ValueError):
            classify_url("https://evil-tiktok.com/@user/video/123")

    def test_cancelling_flow(self):
        queue = TaskQueue(max_concurrent=1)
        try:
            queue._init_task("task-cancel-test")
            queue.cancel_task("task-cancel-test")
            self.assertIn("task-cancel-test", queue.cancelled_tasks)
            status = queue.tasks.get("task-cancel-test", {}).get("status")
            self.assertEqual(status, "CANCELLING")
        finally:
            queue.shutdown()


if __name__ == "__main__":
    unittest.main()
