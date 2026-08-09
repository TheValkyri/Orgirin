"""
ExtractorOrchestrator -- the Strategy + fallback chain from plan §3.

This is the ONLY place that knows both extractors exist. Everything else
in the app (download pipeline, task state machine, UI) talks to this class
and receives a plain TikTokMediaResult, never knowing or caring which
extractor produced it.
"""
from __future__ import annotations

from . import webpage_fetch
from .errors import ContentNotAccessibleError
from .models import TikTokMediaResult
from .parser import parse_page_json


import threading
import time

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, TikTokMediaResult]] = {}
_MAX_CACHE_SIZE = 100
_CACHE_TTL_SEC = 120


def _save_to_cache(cache_key: str, res: TikTokMediaResult) -> None:
    now = time.time()
    with _CACHE_LOCK:
        # Evict expired items
        expired = [k for k, (t, _) in _CACHE.items() if now - t >= _CACHE_TTL_SEC]
        for k in expired:
            _CACHE.pop(k, None)
        # Cap size
        if len(_CACHE) >= _MAX_CACHE_SIZE:
            oldest_key = min(_CACHE.keys(), key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest_key, None)
        _CACHE[cache_key] = (now, res)


class ExtractorOrchestrator:
    def extract(self, url: str, session_cookie: str | None = None, bypass_cache: bool = False) -> TikTokMediaResult:
        cache_key = url.split("?")[0].rstrip("/")
        now = time.time()

        if not bypass_cache:
            with _CACHE_LOCK:
                if cache_key in _CACHE:
                    t, cached_res = _CACHE[cache_key]
                    best_gear = cached_res.best_clean_gear() or cached_res.best_available_gear()
                    is_near_expiry = False
                    if best_gear and best_gear.expires_at > 0 and now >= (best_gear.expires_at - 30):
                        is_near_expiry = True
                    if now - t < _CACHE_TTL_SEC and not is_near_expiry:
                        return cached_res

        try:
            wrapper = webpage_fetch.fetch_and_normalize(url)
            res = parse_page_json(wrapper)
            _save_to_cache(cache_key, res)
            return res
        except ContentNotAccessibleError:
            raise
        except Exception as primary_error:
            # Fallback to yt-dlp if primary scrapers fail or time out
            try:
                res = self._extract_via_ytdlp(url)
                _save_to_cache(cache_key, res)
                return res
            except Exception as secondary_error:
                import logging
                logging.getLogger(__name__).warning(f"yt-dlp TikTok fallback failed: {secondary_error}")
                raise primary_error

    def _extract_via_ytdlp(self, url: str) -> TikTokMediaResult:
        import yt_dlp
        from .models import TikTokMediaGear
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Referer": "https://www.tiktok.com/",
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise RuntimeError("yt-dlp could not extract TikTok info")
            
            post_id = str(info.get("id") or "")
            author = str(info.get("uploader_id") or info.get("uploader") or "user")
            caption = str(info.get("title") or "")
            cover = str(info.get("thumbnail") or "")
            duration = int(info.get("duration") or 0)
            direct_url = str(info.get("url") or "")
            
            gears = []
            if direct_url:
                gears.append(TikTokMediaGear(
                    url=direct_url,
                    width=int(info.get("width") or 1080),
                    height=int(info.get("height") or 1920),
                    codec=str(info.get("vcodec") or "h264"),
                    bitrate=int(info.get("tbr") or 3000000),
                    watermark_status="unknown",
                    source_provider="yt_dlp",
                    mirror_urls=[direct_url],
                ))

            return TikTokMediaResult(
                post_id=post_id,
                post_type="video",
                author_handle=author,
                caption=caption,
                created_at=int(time.time()),
                cover_url=cover,
                duration_sec=duration,
                video_gears=gears,
                source_provider="yt_dlp",
            )

