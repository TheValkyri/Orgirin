import errno
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FFmpegTools:
    """Discovered paths for ffmpeg and ffprobe binaries.

    Each field is the *absolute path to the executable* or ``None`` when the
    binary could not be found.  Callers that need probing must check
    ``ffprobe_bin``; callers that need transformation must check
    ``ffmpeg_bin``.  This avoids masking one tool's absence behind the other.
    """
    ffmpeg_bin: Optional[str] = None
    ffprobe_bin: Optional[str] = None

    @property
    def ffmpeg_dir(self) -> Optional[str]:
        """Directory containing the ffmpeg binary, for yt-dlp ffmpeg_location."""
        if self.ffmpeg_bin:
            return os.path.dirname(self.ffmpeg_bin)
        return None


@dataclass
class ProbeResult:
    """Structured output from a successful ffprobe verification."""
    codec_type: str = ""       # "video" | "audio"
    codec_name: str = ""
    width: int = 0
    height: int = 0
    duration_sec: float = 0.0
    file_size: int = 0
    container: str = ""
    audio_codec: str = ""


class DownloadCancelledException(Exception):
    """Exception raised when a download task is cancelled by user."""

    pass


def get_ffmpeg_path() -> Optional[str]:
    """Returns absolute path to ffmpeg directory if found.

    .. deprecated:: Use :func:`get_ffmpeg_tools` for new code.
    """
    tools = get_ffmpeg_tools()
    return tools.ffmpeg_dir


def get_ffmpeg_tools() -> FFmpegTools:
    """Discover ffmpeg and ffprobe binaries independently.

    Returns an :class:`FFmpegTools` instance where each field is the absolute
    path to the binary or ``None``.  Callers must inspect the specific field
    they need and raise an appropriate error if it is ``None``.
    """
    search_dirs: list[str] = []

    # 1. PyInstaller frozen bundle
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        search_dirs.append(os.path.join(base_dir, "ffmpeg"))

    # 2. packaging/ffmpeg/ relative to workspace
    local_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "packaging", "ffmpeg")
    )
    search_dirs.append(local_dir)

    ffmpeg_bin: Optional[str] = None
    ffprobe_bin: Optional[str] = None

    for d in search_dirs:
        candidate_ffmpeg = os.path.join(d, "ffmpeg.exe")
        candidate_ffprobe = os.path.join(d, "ffprobe.exe")
        if ffmpeg_bin is None and os.path.isfile(candidate_ffmpeg):
            ffmpeg_bin = candidate_ffmpeg
        if ffprobe_bin is None and os.path.isfile(candidate_ffprobe):
            ffprobe_bin = candidate_ffprobe
        if ffmpeg_bin and ffprobe_bin:
            return FFmpegTools(ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin)

    # 3. System PATH fallback
    if ffmpeg_bin is None:
        sys_ffmpeg = shutil.which("ffmpeg")
        if sys_ffmpeg:
            ffmpeg_bin = os.path.abspath(sys_ffmpeg)
    if ffprobe_bin is None:
        sys_ffprobe = shutil.which("ffprobe")
        if sys_ffprobe:
            ffprobe_bin = os.path.abspath(sys_ffprobe)

    return FFmpegTools(ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin)


def _build_output_template(output_dir: str, quality_tag: Optional[str] = None) -> str:
    if quality_tag:
        return os.path.join(output_dir, f"%(title).150B ({quality_tag}).%(ext)s")
    return os.path.join(output_dir, "%(title).180B.%(ext)s")




def _clean_error_msg(msg: str) -> str:
    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(msg))
    clean = re.sub(r'\[0;3\d+m', '', clean)
    clean = re.sub(r'\[0m', '', clean)
    return clean.strip()


def _common_ydl_opts(
    output_dir: str,
    progress_hook: Callable[[dict], None],
    ffmpeg_dir: Optional[str],
    quality_tag: Optional[str] = None,
    postprocessor_hook: Optional[Callable[[dict], None]] = None,
) -> dict:
    opts = {
        "outtmpl": _build_output_template(output_dir, quality_tag),
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "progress_hooks": [progress_hook],
        "noplaylist": True,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "skip_unavailable_fragments": True,
        "socket_timeout": 15,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if postprocessor_hook:
        opts["postprocessor_hooks"] = [postprocessor_hook]
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    return opts


def _make_progress_hook(progress_callback, is_cancelled_check):
    merging_reported = False
    def hook(d):
        nonlocal merging_reported
        if is_cancelled_check and is_cancelled_check():
            raise DownloadCancelledException("Task cancelled by user.")
        if progress_callback and d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            percent = 0.0
            if total > 0:
                percent = round((downloaded / total) * 100, 1)
            elif d.get("fragment_count"):
                frag_idx = d.get("fragment_index", 0)
                frag_cnt = d.get("fragment_count", 1)
                percent = round((frag_idx / frag_cnt) * 100, 1)
            speed = d.get("speed") or 0.0
            speed_kbs = round(speed / 1024.0, 1)
            eta = d.get("eta") or 0
            progress_callback({"percent": percent, "speedKBs": speed_kbs, "etaSec": eta, "status": "DOWNLOADING"})
    return hook


def _make_postprocessor_hook(progress_callback, is_cancelled_check):
    def hook(d):
        if is_cancelled_check and is_cancelled_check():
            raise DownloadCancelledException("Task cancelled by user.")
        if progress_callback and d.get("status") in ("started", "processing"):
            progress_callback({"percent": 99.0, "speedKBs": 0.0, "etaSec": 0, "status": "MERGING"})
    return hook


def _raise_friendly_error(e, media_type="video"):
    if isinstance(e, DownloadCancelledException):
        raise e
    if isinstance(e, yt_dlp.utils.DownloadError):
        if e.exc_info and isinstance(e.exc_info[1], DownloadCancelledException):
            raise e.exc_info[1]
        if "DownloadCancelledException" in str(e) or "Task cancelled by user" in str(e):
            raise DownloadCancelledException("Task cancelled by user.") from e
    if "DownloadCancelledException" in str(e) or "Task cancelled by user" in str(e):
        raise DownloadCancelledException("Task cancelled by user.") from e
    if isinstance(e, OSError) and getattr(e, "errno", None) in (errno.ENOSPC, 28):
        raise RuntimeError("Disk full...") from e
    err_str = _clean_error_msg(str(e))
    if any(phrase in err_str for phrase in ("No space left", "Disk full", "not enough space", "There is not enough space")):
        raise RuntimeError("Disk full...") from e
    raise RuntimeError(f"Lỗi tải {media_type}: {err_str}") from e


def _apply_time_range_and_subs(
    ydl_opts: dict,
    start_sec: Optional[int] = None,
    end_sec: Optional[int] = None,
    sub_lang: Optional[str] = None,
    embed_sub: bool = False,
):
    if start_sec is not None or end_sec is not None:
        s = start_sec if start_sec is not None else 0
        e = end_sec if end_sec is not None else float("inf")
        try:
            from yt_dlp.utils import download_range_func
            ydl_opts["download_ranges"] = download_range_func(None, [(s, e)])
            ydl_opts["force_keyframes_at_cuts"] = True
        except Exception as err:
            logger.warning(f"Failed to set download_ranges: {err}")

    if sub_lang and sub_lang != "none":
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = True
        if sub_lang == "all":
            ydl_opts["subtitleslangs"] = ["vi", "en", ".*"]
        else:
            ydl_opts["subtitleslangs"] = [sub_lang]
        ydl_opts["subtitlesformat"] = "srt/vtt/best"


def vtt_or_srt_to_styled_ass(sub_content: str, play_res_x: int = 1920, play_res_y: int = 1080) -> str:
    """
    Converts VTT or SRT subtitle text content to a modern, beautifully styled ASS subtitle.
    Styles:
    - Font: Futura, Montserrat, Segoe UI, Arial (Geometric Sans-serif / Futura Việt Hóa)
    - Alignment: 2 (Bottom Center - Căn giữa)
    - Position: Raised 75px from bottom (MarginV 75)
    - Box: Semi-transparent dark background box (BorderStyle 3, BackColour &H80000000)
    - Color: Crisp white text (&H00FFFFFF), Bold (-1)
    """
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Futura,46,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,3,0,2,30,30,75,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    blocks = re.split(r'\n\s*\n', sub_content.strip())
    for block in blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if not lines:
            continue
        time_line_idx = -1
        for idx, line in enumerate(lines):
            if '-->' in line:
                time_line_idx = idx
                break
        if time_line_idx == -1:
            continue
        
        time_line = lines[time_line_idx]
        text_lines = lines[time_line_idx + 1:]
        text = " \\N ".join(text_lines)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.strip()
        if not text:
            continue
        
        m = re.search(r'(\d+:)?(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+:)?(\d+):(\d+)[.,](\d+)', time_line)
        if not m:
            continue
        
        sh = int(m.group(1).rstrip(':')) if m.group(1) else 0
        sm = int(m.group(2))
        ss = int(m.group(3))
        sms = int(m.group(4)[:2].ljust(2, '0'))
        
        eh = int(m.group(5).rstrip(':')) if m.group(5) else 0
        em = int(m.group(6))
        es = int(m.group(7))
        ems = int(m.group(8)[:2].ljust(2, '0'))
        
        start_ass = f"{sh}:{sm:02d}:{ss:02d}.{sms:02d}"
        end_ass = f"{eh}:{em:02d}:{es:02d}.{ems:02d}"
        events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")
    
    return ass_header + "\n".join(events) + "\n"


def _postprocess_and_embed_styled_subtitles(output_dir: str, embed_sub: bool = False):
    """
    Finds downloaded VTT/SRT subtitles in output_dir, converts them to modern styled ASS files
    (Futura font, dark background box, centered, raised bottom margin), and optionally embeds
    them into video files via FFmpeg.
    """
    tools = get_ffmpeg_tools()
    out_path = Path(output_dir)
    sub_files = list(out_path.glob("*.vtt")) + list(out_path.glob("*.srt"))

    for sub_file in sub_files:
        try:
            with open(sub_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if not content.strip():
                continue

            ass_content = vtt_or_srt_to_styled_ass(content)
            ass_target = sub_file.with_suffix(".ass")
            with open(ass_target, "w", encoding="utf-8") as f:
                f.write(ass_content)

            logger.info(f"Converted subtitle {sub_file.name} to modern styled ASS (Futura) at {ass_target.name}")

            burned_in_success = False
            if embed_sub and tools.ffmpeg_bin:
                video_files = list(out_path.glob("*.mp4")) + list(out_path.glob("*.mkv"))
                for vid in video_files:
                    if "_subbed" in vid.name:
                        continue
                    temp_subbed = vid.with_name(f"{vid.stem}_subbed{vid.suffix}")
                    escaped_ass_name = ass_target.name.replace("'", "'\\''")
                    # Burn-in ASS subtitles directly into video stream and strip soft sub streams (-sn) to prevent double subtitles
                    cmd = [
                        tools.ffmpeg_bin, "-y",
                        "-i", vid.name,
                        "-vf", f"subtitles='{escaped_ass_name}'",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "18",
                        "-c:a", "copy",
                        "-sn",
                        temp_subbed.name
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=180, cwd=str(out_path))
                    if res.returncode == 0 and temp_subbed.exists() and temp_subbed.stat().st_size > 1000:
                        vid.unlink(missing_ok=True)
                        shutil.move(str(temp_subbed), str(vid))
                        burned_in_success = True
                        logger.info(f"Successfully burned in styled ASS subtitle into {vid.name}")
                    else:
                        err_msg = res.stderr.decode("utf-8", errors="ignore") if isinstance(res.stderr, bytes) else str(res.stderr)
                        logger.warning(f"FFmpeg subtitle burn-in returned code {res.returncode}: {err_msg[:300]}")
                        if temp_subbed.exists():
                            temp_subbed.unlink(missing_ok=True)

            # Cleanup policy:
            # If embed_sub requested and burn-in succeeded: delete ALL external sub files (.vtt, .srt, .ass) so VLC has 0 external files to auto-load.
            # If embed_sub is False: remove raw .vtt/.srt files so ONLY the styled .ass file remains.
            if embed_sub and burned_in_success:
                sub_file.unlink(missing_ok=True)
                ass_target.unlink(missing_ok=True)
                logger.info(f"Cleaned up external sub files {sub_file.name} and {ass_target.name} after successful burn-in")
            elif not embed_sub:
                sub_file.unlink(missing_ok=True)
                logger.info(f"Cleaned up raw sub file {sub_file.name}, leaving styled ASS {ass_target.name}")
        except Exception as exc:
            logger.warning(f"Error styling/embedding subtitle {sub_file.name}: {exc}")


def download_video(
    url: str,
    height: int,
    output_dir: str,
    output_format: str = "best",
    start_sec: Optional[int] = None,
    end_sec: Optional[int] = None,
    sub_lang: Optional[str] = None,
    embed_sub: bool = False,
    progress_callback: Optional[Callable[[dict], None]] = None,
    is_cancelled_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Downloads video at the requested height or lower without re-encoding.
    """
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg_dir = get_ffmpeg_path()
    hook = _make_progress_hook(progress_callback, is_cancelled_check)

    if height <= 0:
        raise ValueError("Độ phân giải tải xuống không hợp lệ.")

    if output_format == "compat":
        fmt_spec = (
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio/"
            f"bestvideo[height<={height}][ext=mp4]+bestaudio/"
            f"bestvideo[width<={height}][vcodec^=avc1]+bestaudio/"
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[width<={height}]/"
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        )
        sort_order = [f"res:{height}", "fps", "vcodec:h264", "vcodec:vp9", "vcodec:av01", "size", "br"]
        merge_fmt = "mp4"
    else:
        fmt_spec = (
            f"bestvideo[height<={height}]+bestaudio/"
            f"bestvideo[height<={height}][ext=mp4]+bestaudio/"
            f"bestvideo[width<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"best[width<={height}]/"
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        )
        sort_order = [f"res:{height}", "fps", "vcodec:av01", "vcodec:vp9", "vcodec:h264", "size", "br"]
        merge_fmt = "mkv"

    quality_tag = f"{height}p {merge_fmt.upper()}"
    if start_sec is not None or end_sec is not None:
        t_start = f"{start_sec}s" if start_sec is not None else "0s"
        t_end = f"{end_sec}s" if end_sec is not None else "end"
        quality_tag += f" Trim {t_start}-{t_end}"

    pp_hook = _make_postprocessor_hook(progress_callback, is_cancelled_check)
    ydl_opts = _common_ydl_opts(output_dir, hook, ffmpeg_dir, quality_tag=quality_tag, postprocessor_hook=pp_hook)
    ydl_opts.update(
        {
            "format": fmt_spec,
            "format_sort": sort_order,
            "merge_output_format": merge_fmt,
            "addmetadata": True,
        }
    )
    _apply_time_range_and_subs(ydl_opts, start_sec, end_sec, sub_lang, embed_sub)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        _postprocess_and_embed_styled_subtitles(output_dir, embed_sub=embed_sub)
        return output_dir
    except DownloadCancelledException:
        raise
    except Exception as e:
        _raise_friendly_error(e, media_type="video")


def download_audio(
    url: str,
    bitrate_kbps: Union[int, str],
    output_dir: str,
    start_sec: Optional[int] = None,
    end_sec: Optional[int] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    is_cancelled_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Downloads audio track and converts it to MP3.
    """
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg_dir = get_ffmpeg_path()
    hook = _make_progress_hook(progress_callback, is_cancelled_check)

    if bitrate_kbps != "original" and bitrate_kbps not in {128, 192, 256, 320}:
        raise ValueError("Bitrate MP3 không hợp lệ.")

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0" if bitrate_kbps == "original" else str(bitrate_kbps),
        }
    ]

    quality_tag = "MP3 VBR Tối đa" if bitrate_kbps == "original" else f"MP3 {bitrate_kbps}kbps"
    if start_sec is not None or end_sec is not None:
        t_start = f"{start_sec}s" if start_sec is not None else "0s"
        t_end = f"{end_sec}s" if end_sec is not None else "end"
        quality_tag += f" Trim {t_start}-{t_end}"

    pp_hook = _make_postprocessor_hook(progress_callback, is_cancelled_check)
    ydl_opts = _common_ydl_opts(output_dir, hook, ffmpeg_dir, quality_tag=quality_tag, postprocessor_hook=pp_hook)
    ydl_opts.update(
        {
            "format": "bestaudio/best",
            "postprocessors": postprocessors,
        }
    )
    _apply_time_range_and_subs(ydl_opts, start_sec, end_sec, sub_lang=None, embed_sub=False)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_dir
    except DownloadCancelledException:
        raise
    except Exception as e:
        _raise_friendly_error(e, media_type="MP3")


def download_audio_native(
    url: str,
    output_dir: str,
    start_sec: Optional[int] = None,
    end_sec: Optional[int] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    is_cancelled_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Downloads audio track in its original format.
    """
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg_dir = get_ffmpeg_path()
    hook = _make_progress_hook(progress_callback, is_cancelled_check)

    quality_tag = "Audio Gốc"
    if start_sec is not None or end_sec is not None:
        t_start = f"{start_sec}s" if start_sec is not None else "0s"
        t_end = f"{end_sec}s" if end_sec is not None else "end"
        quality_tag += f" Trim {t_start}-{t_end}"

    pp_hook = _make_postprocessor_hook(progress_callback, is_cancelled_check)
    ydl_opts = _common_ydl_opts(output_dir, hook, ffmpeg_dir, quality_tag=quality_tag, postprocessor_hook=pp_hook)
    ydl_opts.update(
        {
            "format": "bestaudio/best",
        }
    )
    _apply_time_range_and_subs(ydl_opts, start_sec, end_sec, sub_lang=None, embed_sub=False)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_dir
    except DownloadCancelledException:
        raise
    except Exception as e:
        _raise_friendly_error(e, media_type="audio")


def download_thumbnail(
    url: str,
    output_dir: str,
    progress_callback: Optional[Callable[[dict], None]] = None,
    is_cancelled_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Downloads original high-res thumbnail (WebP / PNG / JPG) from YouTube video.
    """
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg_dir = get_ffmpeg_path()
    hook = _make_progress_hook(progress_callback, is_cancelled_check)

    quality_tag = "Ảnh Bìa Gốc"
    ydl_opts = _common_ydl_opts(output_dir, hook, ffmpeg_dir, quality_tag=quality_tag)
    ydl_opts.update(
        {
            "writethumbnail": True,
            "skip_download": True,
        }
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return output_dir
    except DownloadCancelledException:
        raise
    except Exception as e:
        _raise_friendly_error(e, media_type="ảnh bìa")


def _clean_caption_for_filename(caption: str, max_len: int = 40) -> str:
    if not caption:
        return ""
    text = re.sub(r'#\S+', '', caption)
    text = re.sub(r'@\S+', '', text)
    text = re.sub(r'[\\/*?:"<>|\r\n\t]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        text = re.sub(r'[#@]', '', caption)
        text = re.sub(r'[\\/*?:"<>|\r\n\t]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

    if len(text) > max_len:
        cut = text[:max_len]
        if ' ' in cut:
            text = cut.rsplit(' ', 1)[0].strip()
        else:
            text = cut.strip()
    return text


def _detect_audio_extension(content: bytes, content_type_header: str = "") -> str:
    """Detects real audio file extension from magic bytes and Content-Type header."""
    if content.startswith(b"ID3") or content.startswith(b"\xff\xfb") or content.startswith(b"\xff\xf3"):
        return ".mp3"
    if content.startswith(b"ftyp") or (len(content) > 12 and content[4:8] == b"ftyp"):
        return ".m4a"
    ct = content_type_header.lower()
    if "audio/aac" in ct or "audio/mp4" in ct or "audio/x-m4a" in ct:
        return ".m4a"
    if "audio/mpeg" in ct or "audio/mp3" in ct:
        return ".mp3"
    return ".m4a"


def _verify_downloaded_file(
    file_path: Path,
    media_type: str,  # "video" | "photo" | "audio"
    expected_gear=None,
) -> Optional[ProbeResult]:
    """Fail-closed post-download verification.

    Raises :class:`VerificationMismatchError` on **every** failure path:
    missing file, too-small file, ffprobe unavailable/timeout/non-zero/bad-JSON,
    missing expected streams, resolution mismatch > 30%.

    Returns a :class:`ProbeResult` on success (``None`` for photos).
    """
    from tiktok_extractor.errors import VerificationMismatchError

    if not file_path.exists():
        raise VerificationMismatchError(f"File {file_path.name} không tồn tại sau khi tải.")

    file_size = file_path.stat().st_size
    if file_size < 1000:
        raise VerificationMismatchError(f"File {file_path.name} kích thước quá nhỏ ({file_size} bytes).")

    # --- Photo: magic-byte validation, read errors are FATAL ---
    if media_type == "photo":
        try:
            with open(file_path, "rb") as f:
                header = f.read(12)
        except Exception as e:
            raise VerificationMismatchError(f"Không thể đọc file ảnh {file_path.name}: {e}")
        is_jpeg = header.startswith(b"\xff\xd8\xff")
        is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_webp = header.startswith(b"RIFF") and b"WEBP" in header
        if not (is_jpeg or is_png or is_webp):
            raise VerificationMismatchError(f"File ảnh {file_path.name} có định dạng header không hợp lệ.")
        return None

    # --- Video / Audio: require ffprobe ---
    tools = get_ffmpeg_tools()
    if not tools.ffprobe_bin or not os.path.isfile(tools.ffprobe_bin):
        raise VerificationMismatchError(
            f"Thiếu ffprobe — không thể xác minh file {file_path.name}. "
            "Cài đặt ffprobe hoặc đảm bảo ffprobe.exe nằm cạnh ffmpeg.exe."
        )

    cmd = [
        tools.ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        sub = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=15)
    except subprocess.TimeoutExpired:
        raise VerificationMismatchError(f"ffprobe timed out khi xác minh {file_path.name}.")
    except FileNotFoundError:
        raise VerificationMismatchError(
            f"ffprobe không tìm thấy tại {tools.ffprobe_bin} — không thể xác minh {file_path.name}."
        )

    if sub.returncode != 0:
        raise VerificationMismatchError(
            f"ffprobe trả về mã lỗi {sub.returncode} cho {file_path.name}: {sub.stderr.strip()[:200]}"
        )

    if not sub.stdout or not sub.stdout.strip():
        raise VerificationMismatchError(f"ffprobe không trả về dữ liệu cho {file_path.name}.")

    try:
        info = json.loads(sub.stdout)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise VerificationMismatchError(f"ffprobe trả về dữ liệu JSON không hợp lệ cho {file_path.name}.")

    streams = info.get("streams", [])
    if not streams:
        raise VerificationMismatchError(f"File {file_path.name} không chứa stream phương tiện hợp lệ.")

    fmt_info = info.get("format", {})
    probe = ProbeResult(
        file_size=file_size,
        container=fmt_info.get("format_name", ""),
    )
    try:
        probe.duration_sec = float(fmt_info.get("duration", 0))
    except (ValueError, TypeError):
        probe.duration_sec = 0.0

    if media_type == "video":
        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not v_streams:
            raise VerificationMismatchError(f"File video {file_path.name} không chứa luồng video.")
        v = v_streams[0]
        width = int(v.get("width") or 0)
        height = int(v.get("height") or 0)
        if width <= 0 or height <= 0:
            raise VerificationMismatchError(
                f"File video {file_path.name} có khung hình không hợp lệ ({width}x{height})."
            )
        probe.codec_type = "video"
        probe.codec_name = v.get("codec_name", "")
        probe.width = width
        probe.height = height

        # Audio stream info (optional for video)
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if a_streams:
            probe.audio_codec = a_streams[0].get("codec_name", "")

        # Resolution mismatch check: fail if actual < 70% of expected
        if expected_gear and getattr(expected_gear, "height", 0) > 0:
            expected_h = expected_gear.height
            actual_h = min(width, height) if width < height else height
            if actual_h < expected_h * 0.7:
                raise VerificationMismatchError(
                    f"Độ phân giải thực tế ({actual_h}p) thấp hơn 70% so với mong đợi ({expected_h}p) "
                    f"cho {file_path.name}."
                )

    elif media_type == "audio":
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if not a_streams:
            raise VerificationMismatchError(
                f"File âm thanh {file_path.name} không chứa luồng âm thanh "
                f"(tìm thấy {len(streams)} stream, nhưng không có audio)."
            )
        probe.codec_type = "audio"
        probe.codec_name = a_streams[0].get("codec_name", "")
        probe.audio_codec = probe.codec_name

    return probe


def _trim_file_if_needed(target_path: Path, start_sec: Optional[int], end_sec: Optional[int], media_type: str = "video"):
    if start_sec is None and end_sec is None:
        return
    if start_sec is not None and start_sec < 0:
        raise ValueError("Thời điểm bắt đầu cắt không thể nhỏ hơn 0.")
    if start_sec is not None and end_sec is not None and end_sec <= start_sec:
        raise ValueError("Thời điểm kết thúc cắt phải lớn hơn thời điểm bắt đầu.")
    if not target_path.exists() or target_path.stat().st_size < 100:
        return

    tools = get_ffmpeg_tools()
    if not tools.ffmpeg_bin:
        raise RuntimeError("Thiếu ffmpeg — không thể cắt file.")

    temp_trimmed = target_path.parent / f"trimmed_{target_path.name}"

    ext = target_path.suffix.lower()
    is_audio_only = ext in (".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus") or media_type == "audio"

    cmd = [tools.ffmpeg_bin, "-y"]
    if start_sec is not None:
        cmd.extend(["-ss", str(start_sec)])
    if end_sec is not None:
        cmd.extend(["-to", str(end_sec)])
    cmd.extend(["-i", str(target_path)])

    if is_audio_only:
        cmd.extend(["-c", "copy", str(temp_trimmed)])
    else:
        # Probe original video codec to preserve AV1, VP9, or HEVC (H.265)
        vcodec = "libx264"
        if tools.ffprobe_bin:
            try:
                probe_cmd = [
                    tools.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(target_path),
                ]
                sub_p = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=5)
                c_name = sub_p.stdout.strip().lower()
                if "av1" in c_name:
                    vcodec = "libsvtav1"
                elif "vp9" in c_name:
                    vcodec = "libvpx-vp9"
                elif "hevc" in c_name or "h265" in c_name:
                    vcodec = "libx265"
            except Exception:
                pass

        cmd.extend([
            "-c:v", vcodec,
            "-preset", "ultrafast",
            "-crf", "18",
            "-c:a", "copy",
            str(temp_trimmed),
        ])

    sub = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120)
    if sub.returncode != 0:
        if temp_trimmed.exists():
            temp_trimmed.unlink()
        raise RuntimeError(f"Lỗi FFmpeg khi cắt file ({sub.returncode}): {sub.stderr.strip()[:200]}")

    if temp_trimmed.exists() and temp_trimmed.stat().st_size > 100:
        shutil.move(str(temp_trimmed), str(target_path))
        _verify_downloaded_file(target_path, "audio" if is_audio_only else "video")
    else:
        if temp_trimmed.exists():
            temp_trimmed.unlink()
        raise RuntimeError(f"Cắt file thất bại cho {target_path.name}.")


def download_tiktok_media(
    url: str,
    output_dir: str,
    mode: str = "video",
    height: Optional[int] = None,
    bitrate: Union[int, str, None] = None,
    start_sec: Optional[int] = None,
    end_sec: Optional[int] = None,
    sub_lang: Optional[str] = None,
    embed_sub: bool = False,
    progress_callback: Optional[Callable[[dict], None]] = None,
    is_cancelled_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    Downloads TikTok video (unwatermarked HD), photos/carousel, or audio via ExtractorOrchestrator.
    Performs post-download verification.
    """
    if is_cancelled_check and is_cancelled_check():
        raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")

    os.makedirs(output_dir, exist_ok=True)
    import requests
    import time
    from tiktok_extractor.orchestrator import ExtractorOrchestrator
    from tiktok_extractor.errors import NoCleanGearAvailableError

    orch = ExtractorOrchestrator()
    res = orch.extract(url, bypass_cache=False)

    # A-10: Refresh extraction if signed URLs are expired or near expiry (< 30s)
    best_gear = res.best_clean_gear() or res.best_available_gear()
    if best_gear and best_gear.expires_at > 0 and time.time() >= (best_gear.expires_at - 30):
        logger.info(f"TikTok signed gear URL expired/near-expiry ({best_gear.expires_at}), refreshing extraction...")
        res = orch.extract(url, bypass_cache=True)

    if is_cancelled_check and is_cancelled_check():
        raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }
    clean_author = re.sub(r'[\\/*?:"<>|]', "", res.author_handle or "user")
    short_title = _clean_caption_for_filename(res.caption) or f"post_{res.post_id}"
    base_name = f"[{clean_author}] {short_title}"

    if mode == "photo" or res.post_type == "photo":
        if res.images:
            total = len(res.images)
            downloaded_count = 0
            for idx, img_url in enumerate(res.images, 1):
                if is_cancelled_check and is_cancelled_check():
                    raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")

                downloaded_content = None
                last_err = None
                for attempt in range(3):
                    try:
                        resp = requests.get(img_url, headers=headers, timeout=30)
                        resp.raise_for_status()
                        downloaded_content = resp.content
                        break
                    except Exception as exc:
                        last_err = exc
                        time.sleep(1)

                if downloaded_content is None:
                    raise RuntimeError(f"Không thể tải ảnh {idx}/{total} từ bài viết TikTok carousel: {last_err}")

                ext = ".jpg"
                if downloaded_content.startswith(b"\x89PNG"):
                    ext = ".png"
                elif downloaded_content.startswith(b"RIFF") and b"WEBP" in downloaded_content:
                    ext = ".webp"

                target = Path(output_dir) / f"{base_name} - Photo {idx:02d}{ext}"
                with open(target, "wb") as f:
                    f.write(downloaded_content)

                _verify_downloaded_file(target, "photo")
                downloaded_count += 1

                if progress_callback:
                    progress_callback({
                        "status": "DOWNLOADING",
                        "percent": round((idx / total) * 100, 1),
                        "quality_tag": f"Ảnh HD ({idx}/{total})"
                    })

            if downloaded_count == 0 and total > 0:
                raise RuntimeError("Không thể tải bất kỳ ảnh nào từ bài viết TikTok carousel.")

            if res.music_url:
                try:
                    resp = requests.get(res.music_url, headers=headers, timeout=30)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        audio_ext = _detect_audio_extension(resp.content, resp.headers.get("content-type", ""))
                        music_target = Path(output_dir) / f"{base_name} - Audio Gốc{audio_ext}"
                        with open(music_target, "wb") as f:
                            f.write(resp.content)
                        _trim_file_if_needed(music_target, start_sec, end_sec)
                        _verify_downloaded_file(music_target, "audio")
                except Exception as exc:
                    logger.warning(f"TikTok photo post background music download failed: {exc}")

            return output_dir
        else:
            # Extract Frame 0 from HD video stream first for 1080p Full HD resolution
            target = Path(output_dir) / f"{base_name} - Cover.jpg"
            best_gear = res.best_clean_gear()
            extracted = False
            if best_gear and best_gear.url:
                tools = get_ffmpeg_tools()
                if tools.ffmpeg_bin:
                    try:
                        cmd = [
                            tools.ffmpeg_bin, "-y",
                            "-ss", "00:00:00.5",
                            "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\nReferer: https://www.tiktok.com/\r\n",
                            "-i", best_gear.url,
                            "-vframes", "1",
                            "-q:v", "2",
                            str(target)
                        ]
                        sub = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10)
                        if target.exists() and target.stat().st_size > 1000:
                            extracted = True
                    except Exception as e:
                        logger.warning(f"FFmpeg cover frame extraction failed: {e}")

            if not extracted and res.cover_url:
                resp = requests.get(res.cover_url, headers=headers, timeout=30)
                resp.raise_for_status()
                with open(target, "wb") as f:
                    f.write(resp.content)

            _verify_downloaded_file(target, "photo")

            if progress_callback:
                progress_callback({
                    "status": "DONE",
                    "percent": 100.0,
                    "quality_tag": "Ảnh Bìa Full HD"
                })
            return output_dir

    if mode in ("audio", "audio_native"):
        is_native_requested = (mode == "audio_native" or bitrate == "original" or not bitrate)
        if is_native_requested:
            audio_tag = "Audio Gốc"
        elif str(bitrate).isdigit():
            audio_tag = f"MP3 {bitrate}kbps"
        else:
            audio_tag = f"MP3 {bitrate}"

        if res.music_url and is_native_requested:
            try:
                resp = requests.get(res.music_url, headers=headers, timeout=30)
                if resp.status_code == 200 and len(resp.content) > 500:
                    audio_ext = _detect_audio_extension(resp.content, resp.headers.get("content-type", ""))
                    target = Path(output_dir) / f"{base_name} - {audio_tag}{audio_ext}"
                    with open(target, "wb") as f:
                        f.write(resp.content)
                    _trim_file_if_needed(target, start_sec, end_sec)
                    _verify_downloaded_file(target, "audio")
                    if progress_callback:
                        progress_callback({"status": "DONE", "percent": 100.0, "quality_tag": f"{audio_tag}"})
                    return output_dir
            except Exception as exc:
                logger.warning(f"Direct TikTok music download failed, falling back to FFmpeg: {exc}")

        # Fallback to extracting audio from video stream via FFmpeg
        best_gear = res.best_clean_gear() or res.best_available_gear()
        if not best_gear and not res.music_url:
            if res.video_gears:
                raise NoCleanGearAvailableError("Bài viết TikTok chỉ có luồng video dính watermark. Không thể bóc tách âm thanh từ video watermark.")
            raise RuntimeError("Không tìm thấy luồng âm thanh TikTok.")

        tools = get_ffmpeg_tools()
        if not tools.ffmpeg_bin:
            raise RuntimeError("Thiếu ffmpeg — không thể bóc tách âm thanh từ video TikTok.")

        # Download audio/video stream to local file first using requests (with mirror & token refresh fallback)
        urls_to_try = []
        if res.music_url:
            urls_to_try.append(res.music_url)
        if best_gear:
            urls_to_try.append(best_gear.url)
            urls_to_try.extend(getattr(best_gear, "mirror_urls", []))

        temp_audio_input = Path(output_dir) / "temp_audio_input.tmp"
        downloaded_content = False

        for stream_url in urls_to_try:
            if not stream_url:
                continue
            if is_cancelled_check and is_cancelled_check():
                raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")
            try:
                r = requests.get(stream_url, headers=headers, stream=True, timeout=30)
                if r.status_code in (403, 401) and stream_url == getattr(best_gear, "url", None):
                    try:
                        from tiktok_extractor.webpage_fetch import fetch_wrapper_json
                        from tiktok_extractor.parser import parse_page_json
                        fresh_wrapper = fetch_wrapper_json(url)
                        if fresh_wrapper:
                            fresh_res = parse_page_json(fresh_wrapper)
                            fresh_gear = fresh_res.best_clean_gear() or fresh_res.best_available_gear()
                            if fresh_gear and fresh_gear.url:
                                r = requests.get(fresh_gear.url, headers=headers, stream=True, timeout=30)
                    except Exception:
                        pass
                if r.status_code in (200, 206) and int(r.headers.get("content-length", 0)) > 500:
                    total_size = int(r.headers.get("content-length", 0))
                    downloaded = 0
                    start_t = time.time()
                    with open(temp_audio_input, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if is_cancelled_check and is_cancelled_check():
                                raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_callback and total_size > 0:
                                    el_t = time.time() - start_t
                                    spd = (downloaded / 1024) / el_t if el_t > 0 else 0
                                    pct = min(95.0, round((downloaded / total_size) * 100, 1))
                                    progress_callback({
                                        "status": "DOWNLOADING",
                                        "percent": pct,
                                        "speed_kbs": round(spd, 1),
                                        "eta_sec": int((total_size - downloaded) / (spd * 1024)) if spd > 0 else 0
                                    })
                    if temp_audio_input.exists() and temp_audio_input.stat().st_size > 500:
                        downloaded_content = True
                        break
            except Exception as exc:
                logger.warning(f"Audio stream candidate {stream_url[:50]} failed: {exc}")

        if not downloaded_content or not temp_audio_input.exists():
            raise RuntimeError("Không thể tải luồng dữ liệu âm thanh TikTok từ máy chủ.")

        if is_native_requested:
            audio_ext = ".m4a"  # safe default for AAC
            if tools.ffprobe_bin:
                try:
                    probe_cmd = [
                        tools.ffprobe_bin, "-v", "error",
                        "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        str(temp_audio_input),
                    ]
                    probe_sub = subprocess.run(
                        probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=15
                    )
                    codec = probe_sub.stdout.strip().lower()
                    if codec in ("mp3", "mp3float"):
                        audio_ext = ".mp3"
                    elif codec in ("opus",):
                        audio_ext = ".opus"
                    elif codec in ("vorbis",):
                        audio_ext = ".ogg"
                    elif codec in ("flac",):
                        audio_ext = ".flac"
                except Exception as exc:
                    logger.warning(f"Could not probe local audio codec, defaulting to .m4a: {exc}")

            target = Path(output_dir) / f"{base_name} - {audio_tag}{audio_ext}"
            cmd = [
                tools.ffmpeg_bin, "-y",
                "-i", str(temp_audio_input),
                "-vn", "-map", "0:a:0?", "-c:a", "copy",
                str(target),
            ]
        else:
            target = Path(output_dir) / f"{base_name} - {audio_tag}.mp3"
            bitrate_val = str(bitrate) if str(bitrate).isdigit() else "320"
            cmd = [
                tools.ffmpeg_bin, "-y",
                "-i", str(temp_audio_input),
                "-vn", "-map", "0:a:0?", "-acodec", "libmp3lame", "-b:a", f"{bitrate_val}k",
                str(target),
            ]

        res_sub = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300)
        if temp_audio_input.exists():
            temp_audio_input.unlink()

        if res_sub.returncode != 0:
            raise RuntimeError(
                f"FFmpeg trích xuất âm thanh thất bại (exit {res_sub.returncode}): "
                f"{res_sub.stderr.strip()[:200]}"
            )
        if not target.exists() or target.stat().st_size < 1000:
            raise RuntimeError("Không thể bóc tách luồng âm thanh TikTok.")

        _trim_file_if_needed(target, start_sec, end_sec, "audio")
        _verify_downloaded_file(target, "audio")
        if progress_callback:
            progress_callback({"status": "DONE", "percent": 100.0, "quality_tag": f"{audio_tag}"})
        return output_dir

    # Default video mode -- select specific requested height or best available gear
    selected_gear = None
    if height and res.video_gears:
        for g in res.video_gears:
            if g.watermark_status != "confirmed_watermarked":
                g_h = min(g.width or 0, g.height or 0) if (g.width and g.height) else (g.height or 0)
                if g_h == height or g.height == height or g.width == height:
                    selected_gear = g
                    break
    best_gear = selected_gear or res.best_clean_gear() or res.best_available_gear()
    if not best_gear:
        if res.video_gears:
            raise NoCleanGearAvailableError("Bài viết TikTok chỉ có luồng video dính watermark. Không tải bản dính watermark.")
        raise RuntimeError("Không tìm thấy luồng video TikTok khả dụng.")

    res_tag = f"{best_gear.height}p" if best_gear and best_gear.height else (f"{height}p" if height else "HD")
    target = Path(output_dir) / f"{base_name} - {res_tag}.mp4"

    urls_to_try = [best_gear.url] + getattr(best_gear, "mirror_urls", [])
    unique_urls = []
    for u in urls_to_try:
        if u and u not in unique_urls:
            unique_urls.append(u)

    download_success = False
    last_stream_err = None

    for stream_url in unique_urls:
        if is_cancelled_check and is_cancelled_check():
            raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")
        try:
            r = requests.get(stream_url, headers=headers, stream=True, timeout=30)
            if r.status_code in (403, 401):
                logger.info("TikTok CDN URL returned 403/401 -- refreshing signed token URL via TikWM...")
                try:
                    from tiktok_extractor.webpage_fetch import fetch_wrapper_json
                    from tiktok_extractor.parser import parse_page_json
                    fresh_wrapper = fetch_wrapper_json(url)
                    if fresh_wrapper:
                        fresh_res = parse_page_json(fresh_wrapper)
                        fresh_gear = fresh_res.best_clean_gear() or fresh_res.best_available_gear()
                        if fresh_gear and fresh_gear.url and fresh_gear.url != stream_url:
                            stream_url = fresh_gear.url
                            r = requests.get(stream_url, headers=headers, stream=True, timeout=30)
                except Exception as exc:
                    logger.warning(f"TikWM stream refresh attempt failed: {exc}")
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0

            with open(target, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if is_cancelled_check and is_cancelled_check():
                        raise DownloadCancelledException("Thao tác tải TikTok bị hủy.")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and progress_callback:
                            pct = round((downloaded / total_size) * 100, 1)
                            progress_callback({
                                "status": "DOWNLOADING",
                                "percent": pct,
                                "quality_tag": f"{best_gear.height}p HEVC (No Watermark)" if "h265" in best_gear.codec else f"{best_gear.height}p (No Watermark)"
                            })
            download_success = True
            break
        except DownloadCancelledException:
            raise
        except Exception as err:
            last_stream_err = err
            logger.warning(f"TikTok mirror URL download failed: {err}")

    if not download_success:
        raise RuntimeError(f"Không thể tải luồng video TikTok: {last_stream_err}")

    _verify_downloaded_file(target, "video", expected_gear=best_gear)

    _trim_file_if_needed(target, start_sec, end_sec)

    if res.subtitles and sub_lang and sub_lang != "none":
        try:
            sub_entry = None
            if sub_lang == "auto":
                sub_entry = res.subtitles[0]
            else:
                for s in res.subtitles:
                    if sub_lang.lower() in s.get("lang", "").lower():
                        sub_entry = s
                        break
                if not sub_entry and res.subtitles:
                    sub_entry = res.subtitles[0]

            if sub_entry and sub_entry.get("url"):
                s_resp = requests.get(sub_entry["url"], headers=headers, timeout=15)
                if s_resp.status_code == 200 and len(s_resp.content) > 10:
                    vtt_target = Path(output_dir) / f"{base_name}.vtt"
                    with open(vtt_target, "wb") as f:
                        f.write(s_resp.content)
                    logger.info(f"Downloaded TikTok subtitle ({sub_entry.get('lang')}) to {vtt_target.name}")

                    if embed_sub and tools.ffmpeg_bin and target.exists():
                        embed_target = Path(output_dir) / f"{base_name} - {res_tag} (Sub).mp4"
                        embed_cmd = [
                            tools.ffmpeg_bin, "-y",
                            "-i", str(target),
                            "-i", str(vtt_target),
                            "-c", "copy", "-c:s", "mov_text",
                            str(embed_target)
                        ]
                        sub_res = subprocess.run(embed_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
                        if sub_res.returncode == 0 and embed_target.exists() and embed_target.stat().st_size > 1000:
                            target.unlink(missing_ok=True)
                            shutil.move(str(embed_target), str(target))
                            logger.info(f"Successfully embedded subtitle into {target.name}")
        except Exception as exc:
            logger.warning(f"TikTok subtitle download/embedding failed: {exc}")

    _postprocess_and_embed_styled_subtitles(output_dir, embed_sub=embed_sub)

    if progress_callback:
        progress_callback({"status": "DONE", "percent": 100.0, "quality_tag": "Tải Thành Công"})

    return output_dir


