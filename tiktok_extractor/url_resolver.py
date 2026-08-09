"""
TikTok URL recognition and short-link resolution.

Two kinds of input the app must accept:
  1. Canonical: https://www.tiktok.com/@user/video/7300000000000000000
               https://www.tiktok.com/@user/photo/7300000000000000000
  2. Short-link: https://vt.tiktok.com/XXXXXXXXX/
               https://vm.tiktok.com/XXXXXXXXX/

Short-links 302-redirect to the canonical URL. Per plan §15, prefer
resolving via the redirect Location header (HEAD/no-body request) over a
full page fetch when possible -- cheaper and doesn't require parsing HTML
just to find the canonical URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

_CANONICAL_RE = re.compile(
    r"^/@(?P<author>[\w.\-]+)/(?P<kind>video|photo)/(?P<id>\d+)"
)

@dataclass(frozen=True)
class ParsedTikTokUrl:
    kind: Literal["canonical", "short_link"]
    post_type: Optional[Literal["video", "photo"]]  # None if not yet known
    author: Optional[str]
    post_id: Optional[str]
    original_url: str

def classify_url(url: str) -> ParsedTikTokUrl:
    from urllib.parse import urlsplit
    parts = urlsplit(url.strip())
    
    # Require HTTPS (or HTTP for leniency during redirect resolution)
    if parts.scheme not in ('https', 'http'):
        raise ValueError(f"Not a recognized TikTok URL (scheme {parts.scheme!r}): {url!r}")
    
    hostname = (parts.hostname or '').lower()
    
    # Reject userinfo, private IPs, localhost
    if parts.username or parts.password:
        raise ValueError(f"Not a recognized TikTok URL (has userinfo): {url!r}")
    if hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
        raise ValueError(f"Not a recognized TikTok URL (localhost): {url!r}")
    # Reject private IP ranges
    import ipaddress
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved:
            raise ValueError(f"Not a recognized TikTok URL (private IP): {url!r}")
    except ValueError:
        pass  # hostname is not an IP, continue
    
    allowed_hosts = {'www.tiktok.com', 'tiktok.com', 'vt.tiktok.com', 'vm.tiktok.com'}
    if hostname not in allowed_hosts:
        raise ValueError(f"Not a recognized TikTok URL: {url!r}")
    
    path = parts.path or ''
    
    if hostname in ('www.tiktok.com', 'tiktok.com'):
        m = _CANONICAL_RE.match(path)
        if m:
            return ParsedTikTokUrl(
                kind="canonical",
                post_type=m.group("kind"),  # type: ignore[arg-type]
                author=m.group("author"),
                post_id=m.group("id"),
                original_url=url,
            )
            
    if hostname in ('vt.tiktok.com', 'vm.tiktok.com'):
        return ParsedTikTokUrl(
            kind="short_link",
            post_type=None,
            author=None,
            post_id=None,
            original_url=url,
        )
        
    raise ValueError(f"Not a recognized TikTok URL: {url!r}")

def is_short_link(parsed: ParsedTikTokUrl) -> bool:
    return parsed.kind == "short_link"
