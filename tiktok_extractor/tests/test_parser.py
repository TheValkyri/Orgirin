"""
Golden-fixture tests for Extractor A's parser.

Runs with zero network access -- every fixture is a committed JSON file
under tests/fixtures/. This is the concrete implementation of plan §11's
"golden fixtures" requirement and plan §10's QA checklist.
"""
import json
import time
from pathlib import Path

import pytest

from tiktok_extractor.errors import (
    ContentNotAccessibleError,
    PostNotFoundError,
    SchemaDriftError,
)
from tiktok_extractor.parser import parse_page_json

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Happy path: normal video post
# ---------------------------------------------------------------------------

class TestVideoNormal:
    def setup_method(self):
        self.result = parse_page_json(load_fixture("video_normal.json"))

    def test_basic_fields(self):
        assert self.result.post_type == "video"
        assert self.result.post_id == "7300000000000000001"
        assert self.result.author_handle == "example_user"
        assert self.result.caption == "test caption #fyp"

    def test_all_gears_present(self):
        # 3 bitrateInfo gears + 1 downloadAddr-derived watermarked gear
        assert len(self.result.video_gears) == 4

    def test_gear_selection_picks_highest_res_h265(self):
        """Plan §4 gear selection: max resolution among non-watermarked
        gears, h265 as tiebreak. The fixture has a 1080x1920 h265 gear,
        a 720x1280 h264 gear, and a 576x1024 h264 gear -- the 1080p h265
        one must win even though it isn't the highest raw bitrate entry
        in the list ordering."""
        best = self.result.best_clean_gear()
        assert best is not None
        assert best.width == 1080 and best.height == 1920
        assert best.codec == "h265_hvc1"
        assert best.watermark_status == "confirmed_clean"

    def test_downloadaddr_gear_is_flagged_watermarked(self):
        watermarked = [g for g in self.result.video_gears if g.watermark_status == "confirmed_watermarked"]
        assert len(watermarked) == 1
        assert "download_addr_watermarked" in watermarked[0].url

    def test_best_clean_gear_never_returns_watermarked(self):
        best = self.result.best_clean_gear()
        assert best.watermark_status == "confirmed_clean"

    def test_music_parsed(self):
        assert self.result.music_title == "original sound - example_user"
        assert self.result.music_url is not None
        assert "audio_original.mp3" in self.result.music_url

    def test_expiry_parsed_from_url(self):
        best = self.result.best_clean_gear()
        assert best.expires_at == 9999999999


# ---------------------------------------------------------------------------
# Codec tiebreak, isolated from resolution (regression test)
#
# The video_normal.json test above ("picks_highest_res_h265") does NOT
# actually prove the codec tiebreak works on its own, because in that
# fixture the h265 gear also happens to have the highest resolution --
# resolution alone would produce the same winner even with a broken codec
# comparison. This was caught via mutation testing (deliberately flipping
# the codec_rank values still passed the other test) and is why this
# fixture exists: identical resolution, codec is the ONLY differentiator.
# ---------------------------------------------------------------------------

class TestCodecTiebreak:
    def test_h265_wins_over_h264_at_identical_resolution(self):
        result = parse_page_json(load_fixture("video_codec_tiebreak.json"))
        best = result.best_clean_gear()
        assert best is not None
        assert best.codec == "h265_hvc1"
        assert "h265_1080p" in best.url


# ---------------------------------------------------------------------------
# Gear URL expiry
# ---------------------------------------------------------------------------

class TestGearExpiry:
    def test_expired_gear_is_detectable_by_caller(self):
        result = parse_page_json(load_fixture("video_expired_gear.json"))
        best = result.best_clean_gear()
        assert best is not None
        assert best.expires_at == 1000000000
        # This is intentionally in the past relative to "now" -- caller
        # (download pipeline) is responsible for checking this before use
        # and raising GearURLExpiredError / re-extracting, per plan §4
        # step 3. Assert the data needed to make that decision is correct.
        assert best.expires_at < int(time.time())


# ---------------------------------------------------------------------------
# Watermark-only post: must not silently fall back
# ---------------------------------------------------------------------------

class TestWatermarkOnly:
    def test_no_clean_gear_available(self):
        result = parse_page_json(load_fixture("video_watermark_only.json"))
        assert result.best_clean_gear() is None
        # The single available gear must still be visible (so a caller
        # *could* choose to warn-and-use-it), just never auto-selected.
        assert len(result.video_gears) == 1
        assert result.video_gears[0].watermark_status == "confirmed_watermarked"


# ---------------------------------------------------------------------------
# Photo / carousel posts
# ---------------------------------------------------------------------------

class TestPhotoCarousel:
    def setup_method(self):
        self.result = parse_page_json(load_fixture("photo_carousel.json"))

    def test_post_type_is_photo(self):
        assert self.result.post_type == "photo"

    def test_all_images_present_at_native_resolution_urls(self):
        assert len(self.result.images) == 3
        assert all("img_original" in url for url in self.result.images)

    def test_no_video_gears_for_photo_post(self):
        assert self.result.video_gears == []

    def test_background_music_present(self):
        assert self.result.music_title == "trending audio"
        assert self.result.music_url is not None


# ---------------------------------------------------------------------------
# Error taxonomy (plan §9)
# ---------------------------------------------------------------------------

class TestErrorTaxonomy:
    def test_private_locked_raises_content_not_accessible(self):
        with pytest.raises(ContentNotAccessibleError):
            parse_page_json(load_fixture("private_locked.json"))

    def test_deleted_post_raises_post_not_found_not_schema_drift(self):
        """Critical distinction: an explicit TikTok statusCode signal must
        map to PostNotFoundError, NOT SchemaDriftError -- these need
        different UI messages and different operator response (one is
        normal, one means 'go check if the extractor needs updating')."""
        with pytest.raises(PostNotFoundError):
            parse_page_json(load_fixture("post_deleted.json"))

    def test_reshuffled_wrapper_still_parses_via_walk(self):
        """Proves the fallback walk in _find_item_struct actually works,
        not just the fast path -- this is the whole point of plan §4's
        'walk, don't hardcode a path' requirement."""
        result = parse_page_json(load_fixture("schema_drift_survivable.json"))
        assert result.author_handle == "reshuffled_user"
        assert result.post_id == "7300000000000000006"

    def test_genuinely_unrecognizable_structure_raises_schema_drift(self):
        with pytest.raises(SchemaDriftError):
            parse_page_json(load_fixture("schema_drift_fatal.json"))
