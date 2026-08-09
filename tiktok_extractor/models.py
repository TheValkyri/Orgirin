"""
Unified data contract shared by Extractor A and Extractor B.

Per the architecture plan (§6), NOTHING downstream of the orchestrator is
allowed to branch on which extractor produced a TikTokMediaResult. This file
is the seam that makes the two extractors truly swappable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class TikTokMediaGear:
    """One selectable quality tier for a video post."""

    url: str
    width: int
    height: int
    codec: str  # "h265_hvc1" | "h264" | "unknown"
    bitrate: int
    watermark_status: str = "unknown"  # "confirmed_clean" | "confirmed_watermarked" | "unknown"
    source_provider: str = ""
    expires_at: int = 0  # unix timestamp parsed from the URL's `expire` query
    # param, 0 if it could not be determined. Callers MUST NOT cache/reuse a
    # gear URL past this timestamp (see plan §4, gear selection step 3).
    mirror_urls: list[str] = field(default_factory=list)

    @property
    def resolution_px(self) -> int:
        return self.width * self.height

    @property
    def codec_rank(self) -> int:
        """Higher is better. h265 preserves more detail at equal bitrate,
        so it outranks h264 even when its numeric bitrate is lower."""
        return {"h265_hvc1": 2, "h264": 1}.get(self.codec, 0)


@dataclass(frozen=True)
class TikTokMediaResult:
    post_type: Literal["video", "photo"]
    post_id: str
    author_handle: str
    caption: str
    created_at: int
    duration_sec: int = 0

    video_gears: list[TikTokMediaGear] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    cover_url: Optional[str] = None
    music_url: Optional[str] = None
    music_title: Optional[str] = None
    music_author: Optional[str] = None

    source_extractor: Literal["A", "B"] = "A"
    source_provider: str = ""

    def best_clean_gear(self) -> Optional[TikTokMediaGear]:
        """Implements plan §4 gear-selection logic: highest resolution among
        non-watermarked gears, h265 as tiebreak. Returns None if no clean
        gear exists (caller must surface this explicitly, never silently
        fall back to a watermarked gear -- see plan §9)."""
        clean = [g for g in self.video_gears if g.watermark_status == "confirmed_clean"]
        if not clean:
            return None
        return max(clean, key=lambda g: (g.resolution_px, g.codec_rank))

    def best_available_gear(self) -> Optional[TikTokMediaGear]:
        available = [g for g in self.video_gears if g.watermark_status != "confirmed_watermarked"]
        if not available:
            return None
        return max(available, key=lambda g: (g.resolution_px, g.codec_rank))
