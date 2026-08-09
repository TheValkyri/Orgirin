import logging
import re
from typing import Any, Dict, List, Optional
import yt_dlp

logger = logging.getLogger(__name__)

# Standard resolution targets for YouTube
STANDARD_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]

def _get_best_audio_size(formats_raw: List[dict], duration_sec: int) -> float:
    """Finds best audio format size in MB."""
    best_audio_bytes = 0
    best_abr = 0
    for fmt in formats_raw:
        if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none":
            size = fmt.get("filesize") or fmt.get("filesize_approx")
            if size and size > best_audio_bytes:
                best_audio_bytes = size
            abr = fmt.get("abr") or fmt.get("tbr") or 0
            if abr > best_abr:
                best_abr = abr
    if best_audio_bytes > 0:
        return best_audio_bytes / (1024 * 1024)
    if best_abr > 0 and duration_sec > 0:
        return (best_abr * 1000 / 8 * duration_sec) / (1024 * 1024)
    # Default audio estimate ~ 1.2 MB per minute (160kbps)
    return (duration_sec / 60.0) * 1.2

def _estimate_video_size_mb(fmt: dict, duration_sec: int) -> float:
    size_bytes = fmt.get("filesize") or fmt.get("filesize_approx")
    if size_bytes:
        return size_bytes / (1024 * 1024)
    
    tbr = fmt.get("vbr") or fmt.get("tbr") # bitrate in kbps
    if tbr and duration_sec > 0:
        bytes_est = (tbr * 1000 / 8) * duration_sec
        return bytes_est / (1024 * 1024)
    
    height = fmt.get("height", 720)
    # Realistic VP9/AV1 per-minute averages
    rough_mb_per_min = {
        2160: 18.0,
        1440: 10.0,
        1080: 5.0,
        720: 2.5,
        480: 1.2,
        360: 0.7,
    }
    mb_per_min = rough_mb_per_min.get(height, 3.5)
    return (duration_sec / 60.0) * mb_per_min

def parse_video_formats(formats_raw: List[dict], duration_sec: int) -> List[dict]:
    """
    Groups raw yt-dlp formats by height resolution and returns a deduplicated,
    sorted list of VideoFormat objects descending by height.
    Calculates precise merged (video + audio) estimated file sizes for both
    Best Quality (VP9/AV1) and Compatible Mode (H.264).
    """
    audio_mb = _get_best_audio_size(formats_raw, duration_sec)
    by_height_best: Dict[int, dict] = {}
    by_height_compat: Dict[int, dict] = {}
    
    # Priority for codec selection matching best-quality downloader (VP9/AV1 preferred over H.264)
    def codec_score(vcodec: str) -> int:
        v = (vcodec or "").lower()
        if "av01" in v or "av1" in v:
            return 3
        if "vp9" in v or "vp09" in v:
            return 2
        return 1

    for fmt in formats_raw:
        raw_h = fmt.get("height")
        raw_w = fmt.get("width")
        vcodec = fmt.get("vcodec", "none")
        if not raw_h or vcodec == "none":
            continue

        # For vertical videos (e.g. Shorts 1080x1920), effective resolution height is min(raw_h, raw_w)
        if raw_w and raw_h and raw_w < raw_h:
            height = raw_w
        else:
            height = raw_h
            
        fps = int(fmt.get("fps") or 30)
        acodec = fmt.get("acodec", "none")
        video_mb = _estimate_video_size_mb(fmt, duration_sec)
        total_mb = round(video_mb + audio_mb, 1)
        score = codec_score(vcodec)
        v_lower = vcodec.lower()

        # Track Best Quality stream (VP9/AV1 > H.264)
        if height not in by_height_best:
            by_height_best[height] = {
                "resolutionLabel": f"{height}p",
                "height": height,
                "fps": fps,
                "vcodec": vcodec,
                "acodec": acodec,
                "estimatedSizeMB": total_mb,
                "_score": score,
                "_fps": fps,
            }
        else:
            existing = by_height_best[height]
            if (fps > existing["_fps"]) or (fps == existing["_fps"] and score > existing["_score"]):
                by_height_best[height] = {
                    "resolutionLabel": f"{height}p",
                    "height": height,
                    "fps": fps,
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "estimatedSizeMB": total_mb,
                    "_score": score,
                    "_fps": fps,
                }

        # Track Compatible Mode stream (H.264 / AVC1)
        if "avc1" in v_lower or "h264" in v_lower or "avc" in v_lower:
            if height not in by_height_compat or fps > by_height_compat[height]["_fps"]:
                by_height_compat[height] = {
                    "estimatedSizeCompatMB": total_mb,
                    "_fps": fps,
                }
                
    result = []
    for h in sorted(by_height_best.keys(), reverse=True):
        f = by_height_best[h]
        f.pop("_score", None)
        f.pop("_fps", None)

        compat_info = by_height_compat.get(h)
        if compat_info:
            f["estimatedSizeCompatMB"] = compat_info["estimatedSizeCompatMB"]
        else:
            # Fallback estimation for H.264 if no explicit H.264 stream provided for height
            f["estimatedSizeCompatMB"] = round(f["estimatedSizeMB"] * 2.2, 1)

        result.append(f)
        
    return result

def is_tiktok_url(url: str) -> bool:
    """Check if URL is a valid TikTok URL with proper host validation."""
    if not url:
        return False
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url.strip())
        if parts.scheme not in ('https', 'http'):
            return False
        if parts.username or parts.password:
            return False
        hostname = (parts.hostname or '').lower()
        allowed_hosts = {'www.tiktok.com', 'tiktok.com', 'vt.tiktok.com', 'vm.tiktok.com'}
        return hostname in allowed_hosts
    except Exception:
        return False


def extract_video_info(url: str) -> dict:
    """
    Extracts video metadata from URL using yt-dlp (YouTube) or ExtractorOrchestrator (TikTok).
    Returns a dict matching the TypeScript VideoInfo interface contract.
    """
    url = url.strip()
    if not url:
        raise ValueError("URL rỗng. Dán liên kết YouTube hoặc TikTok vào ô phía trên rồi thử lại.")

    if is_tiktok_url(url):
        return _extract_tiktok_info(url)
    else:
        return _extract_youtube_info(url)


def _extract_tiktok_info(url: str) -> dict:
    try:
        from tiktok_extractor.orchestrator import ExtractorOrchestrator
        orch = ExtractorOrchestrator()
        res = orch.extract(url)
        
        formats = []
        if res.video_gears:
            for gear in res.video_gears:
                # A-11: Portrait resolution normalization (shorter edge)
                w_val = gear.width or 0
                h_val_raw = gear.height or 0
                if w_val > 0 and h_val_raw > 0:
                    h_val = min(w_val, h_val_raw)
                else:
                    h_val = h_val_raw
                
                if gear.watermark_status == "confirmed_clean":
                    wm_label = "(Nguồn báo không WM)"
                elif gear.watermark_status == "unknown":
                    wm_label = "(Chưa xác minh WM)"
                else:
                    wm_label = "(Có Watermark)"

                lbl = f"{h_val}p HEVC {wm_label}" if "h265" in gear.codec else f"{h_val}p H.264 {wm_label}"
                
                if gear.bitrate and gear.bitrate > 0:
                    effective_bitrate = gear.bitrate
                else:
                    effective_bitrate = 1500000 if "h265" in gear.codec else 3000000

                dur_sec = res.duration_sec if (res.duration_sec and res.duration_sec > 0) else 0
                bytes_est = (effective_bitrate / 8) * dur_sec if dur_sec > 0 else 0
                size_mb = round(bytes_est / (1024 * 1024), 1) if bytes_est > 0 else 0.0

                formats.append({
                    "resolutionLabel": lbl,
                    "height": h_val,
                    "fps": 0,
                    "vcodec": gear.codec,
                    "acodec": "unknown",
                    "estimatedSizeMB": size_mb,
                    "estimatedSizeCompatMB": size_mb,
                })
        elif res.images:
            formats.append({
                "resolutionLabel": f"Bộ Ảnh Gốc HD ({len(res.images)} Ảnh)",
                "height": 0,
                "fps": 0,
                "vcodec": "photo",
                "acodec": "none",
                "estimatedSizeMB": round(len(res.images) * 2.5, 1)
            })

        thumb_url = _get_tiktok_b64_thumbnail(res)
        is_photo = res.post_type == "photo" or (not res.video_gears and bool(res.images))

        return {
            "platform": "tiktok",
            "mediaType": "photo" if is_photo else "video",
            "title": res.caption or f"TikTok by @{res.author_handle}",
            "thumbnailUrl": thumb_url,
            "durationSec": res.duration_sec,
            "channel": f"@{res.author_handle}",
            "isPlaylist": False,
            "isYouTubePlaylist": False,
            "capabilities": {
                "supportsSubtitles": False,
                "supportsMp3": True,
                "supportsTrim": True,
                "supportsNativeAudio": True,
            },
            "formats": formats
        }
    except Exception as e:
        logger.error(f"TikTok extraction error: {e}")
        raise RuntimeError(f"Không thể đọc bài viết TikTok: {e}")


def _get_tiktok_b64_thumbnail(res) -> str:
    import base64
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }

    # First check cover_url or images directly via lightweight HTTP fetch
    candidates = []
    if res.cover_url:
        candidates.append(res.cover_url)
    if res.images:
        candidates.extend(res.images)

    for url in candidates:
        if not url or not url.startswith("http"):
            continue
        try:
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200 and len(r.content) > 500:
                b64 = base64.b64encode(r.content).decode("ascii")
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if not mime.startswith("image/"):
                    mime = "image/jpeg"
                return f"data:{mime};base64,{b64}"
        except Exception:
            pass

    # Fallback: extract Frame 0 via FFmpeg if cover_url failed
    best_gear = res.best_clean_gear() or res.best_available_gear() or (res.video_gears[0] if res.video_gears else None)
    if best_gear and best_gear.url:
        try:
            import subprocess, tempfile, os
            from core.downloader import get_ffmpeg_path
            ffmpeg_dir = get_ffmpeg_path()
            ffmpeg_bin = os.path.join(ffmpeg_dir, "ffmpeg.exe") if ffmpeg_dir else "ffmpeg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            os.close(tmp_fd)

            cmd = [
                ffmpeg_bin, "-y",
                "-ss", "00:00:00.5",
                "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\nReferer: https://www.tiktok.com/\r\n",
                "-i", best_gear.url,
                "-vframes", "1",
                "-q:v", "2",
                tmp_path
            ]
            subprocess.run(cmd, capture_output=True, timeout=6)
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 500:
                with open(tmp_path, "rb") as f:
                    content = f.read()
                os.remove(tmp_path)
                b64 = base64.b64encode(content).decode("ascii")
                return f"data:image/jpeg;base64,{b64}"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            logger.warning(f"FFmpeg b64 thumbnail extraction failed: {e}")

    return res.cover_url or ""


def _extract_youtube_info(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 15,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            raw_info = ydl.extract_info(url, download=False)
    except Exception as e:
        raw_err = str(e)
        err_msg = re.sub(r"\x1B\[[0-9;]*[a-zA-Z]", "", raw_err).strip()
        logger.error(f"yt-dlp extract_info error for {url}: {err_msg}")
        if "Private video" in err_msg or "Sign in" in err_msg:
            raise RuntimeError("Video ở chế độ riêng tư (HTTP 403). Cần liên kết công khai hoặc unlisted.")
        elif "Video unavailable" in err_msg:
            raise RuntimeError("Video không tồn tại hoặc đã bị gỡ bỏ.")
        elif "Incomplete YouTube ID" in err_msg or "is not a valid URL" in err_msg:
            raise RuntimeError("URL không hợp lệ. Sao chép lại liên kết đầy đủ từ thanh địa chỉ YouTube.")
        else:
            raise RuntimeError(f"Không thể đọc thông tin video: {err_msg}")
            
    if not raw_info:
        raise RuntimeError("Không tìm thấy dữ liệu video.")
        
    is_playlist = raw_info.get("_type") == "playlist" or "entries" in raw_info
    
    if is_playlist:
        entries_raw = list(raw_info.get("entries") or [])
        playlist_entries = []
        candidate_urls = []
        
        for idx, entry in enumerate(entries_raw):
            if not entry:
                continue
            entry_id = entry.get("id") or f"entry_{idx}"
            entry_title = entry.get("title") or f"Video #{idx + 1}"
            entry_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry_id}"
            if len(candidate_urls) < 3:
                candidate_urls.append(entry_url)
                
            entry_thumb = (
                entry.get("thumbnail")
                or f"https://i.ytimg.com/vi/{entry_id}/hqdefault.jpg"
            )
            playlist_entries.append({
                "id": entry_id,
                "title": entry_title,
                "thumbnailUrl": entry_thumb,
            })
            
        formats = []
        duration_sec = 0
        for cand_url in candidate_urls:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl_single:
                    cand_raw = ydl_single.extract_info(cand_url, download=False)
                    if cand_raw and cand_raw.get("formats"):
                        duration_sec = int(cand_raw.get("duration") or 0)
                        formats = parse_video_formats(cand_raw.get("formats", []), duration_sec)
                        if formats:
                            break
            except Exception as exc:
                logger.warning(f"Error fetching playlist entry formats from {cand_url}: {exc}")
                
        return {
            "platform": "youtube",
            "mediaType": "video",
            "title": raw_info.get("title") or "Danh sách phát YouTube",
            "thumbnailUrl": playlist_entries[0]["thumbnailUrl"] if playlist_entries else "",
            "durationSec": duration_sec or (len(playlist_entries) * 240),
            "channel": raw_info.get("uploader") or raw_info.get("channel") or "YouTube Channel",
            "isPlaylist": True,
            "isYouTubePlaylist": True,
            "capabilities": {
                "supportsSubtitles": True,
                "supportsMp3": True,
                "supportsTrim": True,
                "supportsNativeAudio": True,
            },
            "playlistEntries": playlist_entries,
            "formats": formats,
        }
    else:
        duration_sec = int(raw_info.get("duration") or 0)
        formats = parse_video_formats(raw_info.get("formats", []), duration_sec)
        
        thumb_url = raw_info.get("thumbnail")
        if not thumb_url and raw_info.get("thumbnails"):
            thumb_url = raw_info["thumbnails"][-1].get("url")
        if not thumb_url and raw_info.get("id"):
            thumb_url = f"https://i.ytimg.com/vi/{raw_info['id']}/hqdefault.jpg"
            
        return {
            "platform": "youtube",
            "mediaType": "video",
            "title": raw_info.get("title") or "Video YouTube",
            "thumbnailUrl": thumb_url or "",
            "durationSec": duration_sec,
            "channel": raw_info.get("uploader") or raw_info.get("channel") or "YouTube Channel",
            "isPlaylist": False,
            "isYouTubePlaylist": False,
            "capabilities": {
                "supportsSubtitles": True,
                "supportsMp3": True,
                "supportsTrim": True,
                "supportsNativeAudio": True,
            },
            "formats": formats,
        }

