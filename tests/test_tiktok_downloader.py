import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.downloader import (
    _detect_audio_extension,
    _verify_downloaded_file,
    download_tiktok_media,
)
from core.extractor import is_tiktok_url, extract_video_info
from tiktok_extractor.errors import (
    NoCleanGearAvailableError,
    VerificationMismatchError,
)
from tiktok_extractor.models import TikTokMediaGear, TikTokMediaResult
from tiktok_extractor.orchestrator import ExtractorOrchestrator


class TikTokDownloaderTests(unittest.TestCase):

    def test_is_tiktok_url(self):
        self.assertTrue(is_tiktok_url("https://www.tiktok.com/@user/video/123456789"))
        self.assertTrue(is_tiktok_url("https://vt.tiktok.com/ZS12345/"))
        self.assertTrue(is_tiktok_url("https://vm.tiktok.com/ZMs12345/"))
        self.assertFalse(is_tiktok_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(is_tiktok_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertFalse(is_tiktok_url(""))

    def test_detect_audio_extension(self):
        self.assertEqual(_detect_audio_extension(b"ID3\x04\x00\x00"), ".mp3")
        self.assertEqual(_detect_audio_extension(b"\x00\x00\x00\x18ftypmp42"), ".m4a")
        self.assertEqual(_detect_audio_extension(b"randombytes", "audio/aac"), ".m4a")
        self.assertEqual(_detect_audio_extension(b"randombytes", "audio/mpeg"), ".mp3")

    def test_verify_downloaded_file_raises_on_small_or_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_file = Path(tmp_dir) / "nonexistent.mp4"
            with self.assertRaises(VerificationMismatchError):
                _verify_downloaded_file(missing_file, "video")

            small_file = Path(tmp_dir) / "tiny.mp4"
            small_file.write_bytes(b"too small")
            with self.assertRaises(VerificationMismatchError):
                _verify_downloaded_file(small_file, "video")

    def test_verify_downloaded_file_photo_header(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            img_file = Path(tmp_dir) / "valid.jpg"
            img_file.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 2000)
            # Should not raise exception
            _verify_downloaded_file(img_file, "photo")

            invalid_img = Path(tmp_dir) / "invalid.jpg"
            invalid_img.write_bytes(b"NOT_AN_IMAGE_HEADER" + b"0" * 2000)
            with self.assertRaises(VerificationMismatchError):
                _verify_downloaded_file(invalid_img, "photo")

    def test_raises_no_clean_gear_available_error(self):
        watermarked_gear = TikTokMediaGear(
            url="https://example.test/watermarked.mp4",
            width=720,
            height=1280,
            codec="h264",
            bitrate=3000000,
            watermark_status="confirmed_watermarked",
        )
        result = TikTokMediaResult(
            post_type="video",
            post_id="123",
            author_handle="testuser",
            caption="Test Caption",
            created_at=100000,
            video_gears=[watermarked_gear],
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch("tiktok_extractor.orchestrator.ExtractorOrchestrator.extract", return_value=result):
            with self.assertRaises(NoCleanGearAvailableError):
                download_tiktok_media("https://www.tiktok.com/@testuser/video/123", tmp_dir, mode="video")

    def test_ytdlp_fallback_instantiation(self):
        orch = ExtractorOrchestrator()
        fake_info = {
            "id": "789",
            "uploader_id": "testuser",
            "title": "Fallback Title",
            "thumbnail": "https://example.test/thumb.jpg",
            "duration": 15,
            "url": "https://example.test/direct.mp4",
            "width": 1080,
            "height": 1920,
            "vcodec": "h264",
            "tbr": 4000,
        }

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = fake_info
            mock_ydl.return_value.__enter__.return_value = mock_ydl_instance

            res = orch._extract_via_ytdlp("https://www.tiktok.com/@testuser/video/789")
            self.assertEqual(res.post_id, "789")
            self.assertEqual(len(res.video_gears), 1)
            self.assertEqual(res.video_gears[0].height, 1920)

    def test_extract_video_info_returns_platform(self):
        yt_info = {
            "_type": "video",
            "id": "abc",
            "title": "YouTube Test Video",
            "duration": 120,
            "uploader": "Test Channel",
            "formats": [{"height": 720, "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000000}],
        }
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.extract_info.return_value = yt_info
            mock_ydl.return_value.__enter__.return_value = mock_ydl_instance

            res = extract_video_info("https://www.youtube.com/watch?v=abc")
            self.assertEqual(res["platform"], "youtube")
            self.assertEqual(res["title"], "YouTube Test Video")


if __name__ == "__main__":
    unittest.main()
