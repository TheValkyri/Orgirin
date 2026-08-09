"""
Extractor A -- parsing logic.

Deliberately separated from network I/O (see webpage_fetch.py) so this
module can be fully unit-tested against fixture JSON with zero network
access. This is the module the golden-fixture tests in plan §11 exercise.

Design note on the "walk, don't hardcode a path" requirement (plan §4,
step 2): TikTok has reshuffled the wrapper keys around the item payload
before, while keeping the inner item shape ({id, desc, author, video/
imagePost, ...}) stable. `_find_item_struct` therefore searches the parsed
object for that recognizable inner shape instead of trusting one fixed
key path like data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]
["itemStruct"]. That fixed path is tried first as a fast path, but the
fallback walk is what keeps this resilient to reshuffling specifically.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

from .errors import (
    ContentNotAccessibleError,
    PostNotFoundError,
    SchemaDriftError,
)
from .models import TikTokMediaGear, TikTokMediaResult

# TikTok's "item not found / removed" status codes as surfaced in the
# webapp wrapper's statusCode field. Kept as a set (not a single value)
# because TikTok has used more than one code for variants of "gone".
_NOT_FOUND_STATUS_CODES = {10204, 10222}

# Fast-path key sequence, tried first. If TikTok reshuffles this, the walk
# below still finds the data -- this is purely a cheap optimization, not
# something correctness depends on.
_FAST_PATH_KEYS = (
    "__DEFAULT_SCOPE__",
    "webapp.video-detail",
    "itemInfo",
    "itemStruct",
)


def _get_path(obj: Any, *keys: str) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _looks_like_item_struct(obj: Any) -> bool:
    """Heuristic identification of the item payload, independent of where
    in the wrapper it's nested. An item struct always has an author, an id,
    and exactly one of {video, imagePost} as its media payload."""
    if not isinstance(obj, dict):
        return False
    has_id = isinstance(obj.get("id"), str)
    has_author = isinstance(obj.get("author"), dict)
    has_media = isinstance(obj.get("video"), dict) or isinstance(
        obj.get("imagePost"), dict
    )
    return has_id and has_author and has_media


def _walk_for_item_struct(obj: Any, _depth: int = 0) -> Optional[dict]:
    if _depth > 12:  # guard against pathological/cyclic structures
        return None
    if _looks_like_item_struct(obj):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            found = _walk_for_item_struct(v, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_for_item_struct(v, _depth + 1)
            if found is not None:
                return found
    return None


def _find_status_code(obj: Any, _depth: int = 0) -> Optional[int]:
    """Look for an explicit statusCode near the top of the wrapper,
    used to distinguish an explicit 'item removed' signal (PostNotFoundError)
    from a genuine structural change we don't recognize (SchemaDriftError)."""
    if _depth > 6:
        return None
    if isinstance(obj, dict):
        if "statusCode" in obj and isinstance(obj["statusCode"], int):
            return obj["statusCode"]
        for v in obj.values():
            found = _find_status_code(v, _depth + 1)
            if found is not None:
                return found
    return None


def _find_item_struct(wrapper: dict) -> dict:
    fast = _get_path(wrapper, *_FAST_PATH_KEYS)
    if _looks_like_item_struct(fast):
        return fast

    walked = _walk_for_item_struct(wrapper)
    if walked is not None:
        return walked

    status = _find_status_code(wrapper)
    if status in _NOT_FOUND_STATUS_CODES:
        raise PostNotFoundError(f"TikTok reported status code {status}")

    raise SchemaDriftError(
        "Could not locate item payload in page JSON -- TikTok's page "
        "structure may have changed. Update _looks_like_item_struct / "
        "_FAST_PATH_KEYS in parser.py."
    )


def _extract_expiry(url: str) -> int:
    try:
        qs = parse_qs(urlparse(url).query)
        val = qs.get("expire", ["0"])[0]
        return int(val)
    except (ValueError, IndexError):
        return 0


def _parse_video_gears(video: dict) -> list[TikTokMediaGear]:
    if not isinstance(video, dict):
        return []
    gears: list[TikTokMediaGear] = []

    for entry in video.get("bitrateInfo", []) or []:
        if not isinstance(entry, dict):
            continue
        play_addr = entry.get("PlayAddr")
        if not isinstance(play_addr, dict):
            continue
        url_list = [u for u in (play_addr.get("UrlList") or []) if isinstance(u, str)]
        if not url_list:
            continue
        url = url_list[0]
        has_wm = entry.get("has_watermark")
        if has_wm is None:
            has_wm = entry.get("HasWatermark")
        if has_wm is None:
            has_wm = entry.get("is_watermarked")
        if has_wm is None:
            has_wm = entry.get("isWatermarked")

        if has_wm is True or has_wm == 1:
            watermark_status = "confirmed_watermarked"
        elif has_wm is False or has_wm == 0:
            watermark_status = "confirmed_clean"
        elif entry.get("source_provider") in ("tikwm", "yt_dlp") or entry.get("watermark_status") == "unknown":
            watermark_status = "unknown"
        else:
            # Official TikTok page JSON bitrateInfo playAddr streams default to confirmed_clean
            watermark_status = "confirmed_clean"
            
        gears.append(
            TikTokMediaGear(
                url=url,
                width=int(play_addr.get("Width") or entry.get("Width") or 0),
                height=int(play_addr.get("Height") or entry.get("Height") or 0),
                codec=str(entry.get("CodecType", "unknown")),
                bitrate=int(entry.get("Bitrate", 0)),
                watermark_status=watermark_status,
                source_provider=str(entry.get("source_provider", "")),
                expires_at=_extract_expiry(url),
                mirror_urls=url_list,
            )
        )

    download_addr = video.get("downloadAddr")
    if isinstance(download_addr, str) and download_addr:
        gears.append(
            TikTokMediaGear(
                url=download_addr,
                width=int(video.get("width") or 0),
                height=int(video.get("height") or 0),
                codec="unknown",
                bitrate=0,
                watermark_status="confirmed_watermarked",
                expires_at=_extract_expiry(download_addr),
            )
        )

    return gears


def _parse_images(image_post: dict) -> list[str]:
    if not isinstance(image_post, dict):
        return []
    urls: list[str] = []
    for img in image_post.get("images", []) or []:
        if not isinstance(img, dict):
            continue
        image_url = img.get("imageURL") or img.get("ImageURL")
        if not isinstance(image_url, dict):
            continue
        url_list = image_url.get("urlList") or image_url.get("UrlList") or []
        if isinstance(url_list, list) and url_list and isinstance(url_list[0], str):
            urls.append(url_list[0])
    return urls


def _parse_music(item: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not isinstance(item, dict):
        return None, None, None
    music = item.get("music")
    if not isinstance(music, dict):
        return None, None, None
    play_url = music.get("playUrl") or music.get("PlayUrl")
    url_list = play_url.get("urlList") or play_url.get("UrlList") or [] if isinstance(play_url, dict) else []
    url = url_list[0] if isinstance(url_list, list) and url_list and isinstance(url_list[0], str) else None
    title = str(music.get("title")) if music.get("title") else None
    author = str(music.get("authorName")) if music.get("authorName") else None
    return url, title, author


def _parse_cover(video: dict) -> Optional[str]:
    if not isinstance(video, dict):
        return None
    for key in ("originCover", "cover", "dynamicCover"):
        c = video.get(key)
        if isinstance(c, dict):
            url_list = c.get("urlList") or c.get("UrlList") or []
            if isinstance(url_list, list) and url_list and isinstance(url_list[0], str):
                return url_list[0]
        elif isinstance(c, str) and c:
            return c
    return None


def parse_page_json(wrapper: Union[str, dict, None]) -> TikTokMediaResult:
    """Top-level entry point. `wrapper` is the already json.loads()'d
    contents of the __UNIVERSAL_DATA_FOR_REHYDRATION__ script tag.

    Raises (see errors.py, each maps to a plan §9 table row):
      PostNotFoundError, SchemaDriftError, ContentNotAccessibleError
    Does NOT raise NoCleanGearAvailableError -- that's a gear-selection-time
    concern (call result.best_clean_gear() and check for None), not a
    parse-time concern, since a result with only watermarked gears is still
    a successfully *parsed* result.
    """
    if wrapper is None:
        raise SchemaDriftError("Cannot parse None payload -- TikTok wrapper data is missing or empty.")
    if isinstance(wrapper, str):
        if not wrapper.strip():
            raise SchemaDriftError("Cannot parse empty JSON string.")
        try:
            wrapper = json.loads(wrapper)
        except Exception as err:
            raise SchemaDriftError(f"Failed to decode JSON string: {err}")
    if not isinstance(wrapper, dict):
        raise SchemaDriftError(f"Unexpected wrapper type {type(wrapper).__name__}, expected dict.")

    item = _find_item_struct(wrapper)

    video = item.get("video") if isinstance(item, dict) else {}
    image_post = item.get("imagePost") if isinstance(item, dict) else {}

    video_gears = _parse_video_gears(video) if video else []
    images = _parse_images(image_post) if image_post else []

    if not video_gears and not images:
        raise ContentNotAccessibleError(
            "Item payload present but contains no media -- likely private, "
            "region-locked, or age-gated for this session."
        )

    music_url, music_title, music_author = _parse_music(item)

    author = item.get("author") if isinstance(item, dict) else {}
    author_handle = str(author.get("uniqueId", "")) if isinstance(author, dict) else ""

    raw_dur = video.get("duration") if isinstance(video, dict) else (item.get("duration") if isinstance(item, dict) else 0)
    dur_sec = int(raw_dur or 0)
    if dur_sec > 10000:
        dur_sec = dur_sec // 1000

    subtitles = []
    if isinstance(video, dict):
        sub_list = video.get("subtitleInfos") or video.get("subtitleInfo") or []
        for s in sub_list:
            if isinstance(s, dict) and s.get("Url"):
                lang_code = str(s.get("LanguageCodeName") or s.get("Language") or "unknown")
                subtitles.append({
                    "lang": lang_code,
                    "url": s.get("Url"),
                    "format": str(s.get("Format", "webvtt")).lower(),
                })

    return TikTokMediaResult(
        post_type="photo" if images else "video",
        post_id=str(item.get("id", "")) if isinstance(item, dict) else "",
        author_handle=author_handle,
        caption=str(item.get("desc", "")) if isinstance(item, dict) else "",
        created_at=int(item.get("createTime", 0) or 0) if isinstance(item, dict) else 0,
        duration_sec=dur_sec,
        video_gears=video_gears,
        images=images,
        subtitles=subtitles,
        cover_url=_parse_cover(video),
        music_url=music_url,
        music_title=music_title,
        music_author=music_author,
        source_extractor="A",
    )
