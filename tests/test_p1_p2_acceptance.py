import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import downloader, extractor
from core.downloader import _trim_file_if_needed, download_tiktok_media
from core.extractor import extract_video_info
from shell.qt_bridge import _redact_url
from tiktok_extractor.models import TikTokMediaGear, TikTokMediaResult
from tiktok_extractor.orchestrator import ExtractorOrchestrator


class TestP1P2Acceptance(unittest.TestCase):
    """P1 and P2 Remediation Acceptance Tests."""

    def test_trim_validation_negative_start_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "video.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(ValueError) as ctx:
                _trim_file_if_needed(file_path, start_sec=-5, end_sec=10)
            self.assertIn("nhỏ hơn 0", str(ctx.exception))

    def test_trim_validation_end_before_start_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "video.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(ValueError) as ctx:
                _trim_file_if_needed(file_path, start_sec=10, end_sec=5)
            self.assertIn("lớn hơn thời điểm bắt đầu", str(ctx.exception))

    def test_trim_ffmpeg_nonzero_exit_raises_runtime_error(self):
        tools = downloader.get_ffmpeg_tools()
        if not tools.ffmpeg_bin:
            self.skipTest("ffmpeg binary not available")

        mock_sub = MagicMock(returncode=1, stderr="Invalid data found when processing input")
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", return_value=mock_sub):
            file_path = Path(tmp) / "video.mp4"
            file_path.write_bytes(b"\x00" * 2000)
            with self.assertRaises(RuntimeError) as ctx:
                _trim_file_if_needed(file_path, start_sec=1, end_sec=3)
            self.assertIn("Lỗi FFmpeg khi cắt file", str(ctx.exception))

    def test_portrait_metadata_normalization(self):
        res = TikTokMediaResult(
            post_type="video",
            post_id="123",
            author_handle="testuser",
            caption="test",
            created_at=0,
            video_gears=[
                TikTokMediaGear(
                    url="http://1",
                    width=1080,
                    height=1920,
                    codec="h264",
                    bitrate=3000000,
                    watermark_status="confirmed_clean"
                )
            ]
        )
        with patch("tiktok_extractor.orchestrator.ExtractorOrchestrator.extract", return_value=res):
            info = extract_video_info("https://www.tiktok.com/@testuser/video/123")
            fmt = info["formats"][0]
            # Height should be normalized to shorter edge (1080), label should be 1080p
            self.assertEqual(fmt["height"], 1080)
            self.assertIn("1080p H.264", fmt["resolutionLabel"])
            self.assertEqual(info["capabilities"]["supportsSubtitles"], False)
            self.assertEqual(info["capabilities"]["supportsMp3"], True)

    def test_carousel_strict_batching_fails_on_image_error(self):
        res = TikTokMediaResult(
            post_type="photo",
            post_id="789",
            author_handle="testuser",
            caption="carousel test",
            created_at=0,
            images=["https://example.test/img1.jpg", "https://example.test/img2.jpg"]
        )

        def mock_get(url, **kwargs):
            if "img2.jpg" in url:
                raise Exception("HTTP 500 Server Error")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"\xff\xd8\xff\xe0" + b"\x00" * 2000
            resp.raise_for_status = lambda: None
            return resp

        with tempfile.TemporaryDirectory() as tmp, \
             patch("tiktok_extractor.orchestrator.ExtractorOrchestrator.extract", return_value=res), \
             patch("requests.get", side_effect=mock_get):
            with self.assertRaises(RuntimeError) as ctx:
                download_tiktok_media("https://www.tiktok.com/@testuser/video/789", tmp, mode="photo")
            self.assertIn("Không thể tải ảnh 2/2", str(ctx.exception))

    def test_log_redaction_strips_url_query_tokens(self):
        raw_url = "https://v16-webapp-prime.tiktok.com/video/tos/play.mp4?expire=123456&sig=abc789def#section"
        redacted = _redact_url(raw_url)
        self.assertEqual(redacted, "https://v16-webapp-prime.tiktok.com/video/tos/play.mp4")
        self.assertNotIn("expire", redacted)
        self.assertNotIn("sig", redacted)

    def test_dead_code_extractor_b_stub_removed(self):
        stub_path = Path("tiktok_extractor/extractor_b_stub.py")
        self.assertFalse(stub_path.exists())


if __name__ == "__main__":
    unittest.main()
