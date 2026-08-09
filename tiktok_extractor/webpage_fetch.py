"""
Network I/O for Extractor A.

Deliberately thin: this module's only job is (short-link -> canonical URL)
and (canonical URL -> raw wrapper dict). All parsing/selection logic lives
in parser.py and is tested without touching this module at all. Keeping
that boundary is what lets the golden-fixture tests run with zero network
access and zero flakiness from TikTok's side.

NOTE for the implementing agent: this module has NOT been exercised against
live TikTok traffic in this sandbox (outbound network here is allow-listed
to package registries only, tiktok.com is not reachable from it). Treat the
HTML-extraction regex and headers below as a correct-per-spec starting
point per the researched page structure, and verify/adjust against a real
response during Phase 1 implementation before considering this module done.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse

import requests

logger = logging.getLogger(__name__)

from .errors import SchemaDriftError
from .url_resolver import ParsedTikTokUrl, classify_url

_SCRIPT_TAG_RE = re.compile(
    r'<script[^>]*id=["\']?(__UNIVERSAL_DATA_FOR_REHYDRATION__|__NEXT_DATA__|SIGI_STATE|__INIT_DATA__)["\']?[^>]*>(.*?)</script>',
    re.DOTALL,
)

_UA_CANDIDATES = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

_HEADERS = {
    "User-Agent": _UA_CANDIDATES[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
}

_TIMEOUT_S = 15


def resolve_short_link(url: str, session: requests.Session | None = None) -> str:
    """Follow a vt./vm.tiktok.com short link to its canonical URL via the
    redirect chain, without downloading the full page body (plan §15)."""
    sess = session or requests.Session()
    resp = sess.head(
        url, headers=_HEADERS, allow_redirects=True, timeout=_TIMEOUT_S
    )
    resp.raise_for_status()
    resolved_url = resp.url
    classify_url(resolved_url)
    return resolved_url


def fetch_wrapper_json(
    canonical_url: str, session: requests.Session | None = None
) -> dict:
    """Fetch a canonical post page and extract the parsed
    __UNIVERSAL_DATA_FOR_REHYDRATION__ payload as a Python dict."""
    sess = session or requests.Session()

    for ua in _UA_CANDIDATES:
        headers = dict(_HEADERS)
        headers["User-Agent"] = ua
        try:
            resp = sess.get(canonical_url, headers=headers, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 10000:
                match = _SCRIPT_TAG_RE.search(resp.text)
                if match:
                    raw = match.group(2).strip()
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.warning(f"TikTok HTML page fetch with UA {ua[:30]}... failed/timed out: {e}")

    # Fallback via TikWM API (POST & GET) if inline script tag is missing/bot-interstitial
    clean_url = canonical_url.split("?")[0]
    encoded_url = urllib.parse.quote(clean_url, safe="")
    
    tikwm_attempts = [
        ("POST", "https://www.tikwm.com/api/", {"url": clean_url, "hd": 1}),
        ("GET", f"https://api.tikwm.com/api/?url={encoded_url}&hd=1", None),
        ("GET", f"https://www.tikwm.com/api/?url={encoded_url}&hd=1", None),
    ]

    for method, endpoint, payload in tikwm_attempts:
        try:
            if method == "POST":
                api_resp = sess.post(
                    endpoint,
                    data=payload,
                    headers={"User-Agent": _UA_CANDIDATES[0]},
                    timeout=8
                ).json()
            else:
                api_resp = sess.get(
                    endpoint,
                    headers={"User-Agent": _UA_CANDIDATES[0]},
                    timeout=8
                ).json()

            if api_resp.get("code") == 0 and api_resp.get("data"):
                data = api_resp["data"]
                author_obj = data.get("author") if isinstance(data.get("author"), dict) else {}
                music_obj = data.get("music_info") if isinstance(data.get("music_info"), dict) else {}
                return {
                    "__DEFAULT_SCOPE__": {
                        "webapp.video-detail": {
                            "itemInfo": {
                                "itemStruct": {
                                    "id": str(data.get("id", "")),
                                    "desc": data.get("title", ""),
                                    "createTime": data.get("create_time", 0),
                                    "author": {
                                        "uniqueId": author_obj.get("unique_id", "user"),
                                        "nickname": author_obj.get("nickname", "User")
                                    },
                                    "music": {
                                        "title": music_obj.get("title"),
                                        "authorName": music_obj.get("author"),
                                        "playUrl": {"urlList": [data.get("music")]} if data.get("music") else {}
                                    },
                                    "video": {
                                        "originCover": data.get("cover") or data.get("origin_cover"),
                                        "duration": data.get("duration", 0),
                                        "bitrateInfo": [
                                            {
                                                "GearName": "hd_play",
                                                "CodecType": "unknown",
                                                "Bitrate": 0,
                                                "source_provider": "tikwm",
                                                "PlayAddr": {
                                                    "UrlList": [data.get("hdplay") or data.get("play")],
                                                    "Width": 0,
                                                    "Height": 0
                                                }
                                            }
                                        ] if (not data.get("images") and (data.get("hdplay") or data.get("play"))) else [],
                                        "playAddr": {"UrlList": [data.get("wmplay")]} if data.get("wmplay") else {}
                                    },
                                    "imagePost": {
                                        "images": [{"imageURL": {"urlList": [img]}} for img in data.get("images", [])]
                                    } if data.get("images") else None
                                }
                            }
                        }
                    }
                }
        except Exception as exc:
            logger.warning(f"TikWM {method} {endpoint} fallback failed: {exc}")

    raise SchemaDriftError(
        "__UNIVERSAL_DATA_FOR_REHYDRATION__ script tag not found in "
        "page response and TikWM fallbacks failed."
    )


def fetch_and_normalize(url: str, session: requests.Session | None = None) -> dict:
    """Convenience entry point: accepts either a canonical or short-link
    TikTok URL, returns the raw wrapper dict ready for parser.parse_page_json."""
    sess = session or requests.Session()
    parsed: ParsedTikTokUrl = classify_url(url)
    canonical = (
        resolve_short_link(url, sess) if parsed.kind == "short_link" else url
    )
    return fetch_wrapper_json(canonical, sess)
