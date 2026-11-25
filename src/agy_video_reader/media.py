"""Video metadata inspection, subtitle extraction, 480p download, compression, and keyframe extraction."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yt_dlp

from .cache import (
    COOKIES_FILE,
    ensure_cache_dir,
    evict_lru_cache,
    touch_cache_entry,
)
from .settings import load_settings

SUBTITLE_LANG_PRIORITY = ("zh-Hans", "zh-CN", "zh", "ai-zh", "zh-Hant", "en", "en-US")


def find_ffmpeg() -> str | None:
    """Locate ffmpeg executable from PATH or common Windows install locations."""
    found = shutil.which("ffmpeg")
    if found:
        return found

    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates: list[Path] = []
    if local_app:
        local_programs = Path(local_app) / "Programs" / "ffmpeg"
        if local_programs.is_dir():
            candidates.extend(local_programs.rglob("ffmpeg.exe"))
        winget_links = Path(local_app) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        candidates.append(winget_links)
        winget_pkgs = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            for p in winget_pkgs.glob("*FFmpeg*"):
                candidates.extend(p.rglob("ffmpeg.exe"))

    for common in (
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
    ):
        candidates.append(common)

    for cand in candidates:
        if cand.is_file():
            return str(cand)

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    return None


def find_ffprobe() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        cand = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if cand.is_file():
            return str(cand)
    return None


def parse_time_to_seconds(value: str | int | float | None) -> float | None:
    """Parse seconds or HH:MM:SS / MM:SS string into float seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("Time cannot be negative.")
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    parts = text.split(":")
    if len(parts) == 2:
        m, s = float(parts[0]), float(parts[1])
        return m * 60.0 + s
    if len(parts) == 3:
        h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
        return h * 3600.0 + m * 60.0 + s
    raise ValueError(f"Unsupported time format: {value!r}. Use seconds or MM:SS / HH:MM:SS.")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    sec = max(0, int(round(seconds)))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class _SilentYdlLogger:
    """Route all yt-dlp messages away from stdout to keep MCP stdio clean."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        print(msg, file=sys.stderr)


def _base_ydl_opts(url: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "logger": _SilentYdlLogger(),
        "external_downloader_args": {
            "ffmpeg_i": ["-hide_banner", "-loglevel", "error"],
            "ffmpeg": ["-hide_banner", "-loglevel", "error"],
        },
    }
    if url and re.search(r"(bilibili\.com|b23\.tv)", url, re.IGNORECASE):
        # Domestic Bilibili endpoints work most reliably via direct connection
        # even when stale HTTP_PROXY env vars exist.
        opts["proxy"] = ""
    if COOKIES_FILE.is_file():
        opts["cookiefile"] = str(COOKIES_FILE)
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path
        ff_dir = str(Path(ffmpeg_path).parent)
        cur_path = os.environ.get("PATH", "")
        if ff_dir.lower() not in cur_path.lower():
            os.environ["PATH"] = ff_dir + os.pathsep + cur_path
    return opts


def inspect_video(url: str) -> dict[str, Any]:
    """Quickly fetch video metadata and available subtitles without downloading media."""
    opts = _base_ydl_opts(url)
    opts["skip_download"] = True

    try:
        with contextlib.redirect_stdout(sys.stderr):
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return {
            "status": "error",
            "url": url,
            "error": f"Failed to inspect video: {exc}",
            "note": (
                "If this video requires login, is paywalled, or triggers anti-bot/CAPTCHA checks, "
                "do NOT attempt to bypass it. Only public videos or user-provided cookies.txt are supported."
            ),
        }

    if not isinstance(info, dict):
        return {
            "status": "error",
            "url": url,
            "error": "No metadata returned by yt-dlp.",
        }

    cfg = load_settings()
    duration_threshold = int(cfg["max_duration_seconds"])
    duration = info.get("duration")
    exceeds = bool(duration is not None and float(duration) > duration_threshold)
    subtitles = sorted((info.get("subtitles") or {}).keys())
    auto_captions = sorted((info.get("automatic_captions") or {}).keys())
    description = (info.get("description") or "").strip()
    if len(description) > 1500:
        description = description[:1500] + "... [truncated]"

    if exceeds:
        rec = (
            f"Video duration ({format_duration(duration)}) exceeds {duration_threshold}s threshold. "
            "Do NOT download the full video. Inspect subtitles/comments first or call fetch_video_bundle "
            f"with start_time and end_time (<= {duration_threshold}s per slice)."
        )
    else:
        rec = "Duration is within threshold. Call fetch_video_bundle(url) to download lightweight media."

    return {
        "status": "ok",
        "url": url,
        "webpage_url": info.get("webpage_url") or url,
        "platform": info.get("extractor_key") or info.get("extractor") or "unknown",
        "id": info.get("id"),
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or "unknown",
        "upload_date": info.get("upload_date"),
        "duration_seconds": duration,
        "duration_formatted": format_duration(duration),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "description": description,
        "available_subtitles": subtitles,
        "available_automatic_captions": auto_captions[:20],
        "duration_threshold_seconds": duration_threshold,
        "exceeds_duration_threshold": exceeds,
        "active_settings": {
            "max_duration_seconds": duration_threshold,
            "max_height": int(cfg["max_height"]),
            "max_size_mb": int(cfg["max_size_mb"]),
            "default_mode": cfg["default_mode"],
        },
        "ffmpeg_available": bool(find_ffmpeg()),
        "used_cookies": COOKIES_FILE.is_file(),
        "recommended_action": rec,
    }


def _parse_subtitle_file(path: Path) -> str:
    """Convert .vtt or .srt subtitle file into deduplicated plain text."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    lines: list[str] = []
    last_line = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("WEBVTT") or s.startswith("Kind:") or s.startswith("Language:"):
            continue
        if re.fullmatch(r"\d+", s):
            continue
        if "-->" in s:
            continue
        # Strip inline HTML/VTT tags
        s = re.sub(r"<[^>]+>", "", s).strip()
        if not s or s == last_line:
            continue
        lines.append(s)
        last_line = s

    text = "\n".join(lines)
    if len(text) > 12000:
        text = text[:12000] + "\n... [subtitle truncated]"
    return text


def _pick_best_subtitle(entry_dir: Path) -> tuple[str, str | None]:
    sub_files = list(entry_dir.glob("*.vtt")) + list(entry_dir.glob("*.srt"))
    if not sub_files:
        return "", None

    def score(p: Path) -> int:
        name = p.name.lower()
        for idx, lang in enumerate(SUBTITLE_LANG_PRIORITY):
            if f".{lang.lower()}." in name:
                return idx
        return len(SUBTITLE_LANG_PRIORITY) + 1

    sub_files.sort(key=score)
    chosen = sub_files[0]
    return _parse_subtitle_file(chosen), str(chosen)


def _probe_duration(video_path: Path) -> float | None:
    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            val = proc.stdout.strip()
            if val:
                return float(val)
        except Exception:
            pass

    # Fallback: parse Duration from ffmpeg -i stderr
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-i", str(video_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return None


def _compress_to_mp4(
    src_path: Path,
    dst_path: Path,
    duration_hint: float | None = None,
    max_height: int = 480,
    max_video_bytes: int = 25 * 1024 * 1024,
) -> Path:
    """Compress/transcode video with ffmpeg to <= max_height H.264 MP4 under max_video_bytes."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is not installed or not found on PATH; cannot transcode/compress video."
        )

    dur = _probe_duration(src_path) or duration_hint or 60.0
    dur = max(1.0, float(dur))

    # Leave ~12% safety margin under max_video_bytes
    target_bytes = int(max_video_bytes * 0.88)
    audio_kbps = 64
    total_kbps = int((target_bytes * 8) / dur / 1000)
    video_kbps = max(120, min(1800, total_kbps - audio_kbps))

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_path),
        "-vf",
        f"scale=-2:'min({max_height},ih)'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{video_kbps}k",
        "-maxrate",
        f"{int(video_kbps * 1.2)}k",
        "-bufsize",
        f"{int(video_kbps * 2)}k",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        f"{audio_kbps}k",
        "-movflags",
        "+faststart",
        str(dst_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
    if proc.returncode != 0 or not dst_path.is_file():
        raise RuntimeError(f"ffmpeg compression failed: {proc.stderr[-600:]}")

    # Second pass if still over max_video_bytes
    if dst_path.stat().st_size > max_video_bytes:
        smaller_dst = dst_path.with_name("video_small.mp4")
        video_kbps_2 = max(90, int(video_kbps * 0.6))
        fallback_h = min(360, max_height)
        cmd2 = [
            ffmpeg,
            "-y",
            "-i",
            str(src_path),
            "-vf",
            f"scale=-2:'min({fallback_h},ih)'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{video_kbps_2}k",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "48k",
            "-movflags",
            "+faststart",
            str(smaller_dst),
        ]
        proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=240, check=False)
        if proc2.returncode == 0 and smaller_dst.is_file():
            smaller_dst.replace(dst_path)

    return dst_path


def _extract_keyframes(
    video_path: Path,
    frames_dir: Path,
    duration_hint: float | None = None,
    num_frames: int = 8,
) -> list[str]:
    """Extract evenly spaced JPEG keyframes for non-video-capable agents."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to extract keyframes.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    dur = _probe_duration(video_path) or duration_hint or 30.0
    dur = max(1.0, float(dur))

    num_frames = max(4, min(20, int(num_frames)))
    fps_val = num_frames / dur
    out_pattern = str(frames_dir / "frame_%02d.jpg")

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps_val:.6f},scale='min(720,iw)':-2",
        "-frames:v",
        str(num_frames),
        "-q:v",
        "4",
        out_pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg keyframe extraction failed: {proc.stderr[-600:]}")

    frames = sorted(str(p) for p in frames_dir.glob("frame_*.jpg"))
    return frames


def _build_storyboard(
    frame_paths: list[str],
    out_path: Path,
    duration_sec: float,
    start_offset: float = 0.0,
) -> str | None:
    """Combine evenly spaced keyframes into a single timestamped storyboard JPEG."""
    if not frame_paths:
        return None
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    imgs: list[Image.Image] = []
    for fp in frame_paths:
        try:
            with Image.open(fp) as im:
                imgs.append(im.convert("RGB"))
        except Exception:
            continue
    if not imgs:
        return None

    n = len(imgs)
    cols = 2 if n <= 4 else (3 if n <= 9 else 4)
    rows = (n + cols - 1) // cols

    cell_w = 420
    aspect = imgs[0].height / max(1, imgs[0].width)
    cell_h = max(180, min(420, int(round(cell_w * aspect))))
    pad = 6

    sheet_w = cols * cell_w + (cols + 1) * pad
    sheet_h = rows * cell_h + (rows + 1) * pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 24))
    draw = ImageDraw.Draw(sheet)

    for idx, im in enumerate(imgs):
        r, c = divmod(idx, cols)
        resized = im.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        x0 = pad + c * (cell_w + pad)
        y0 = pad + r * (cell_h + pad)
        sheet.paste(resized, (x0, y0))

        # Timestamp badge at top-left of each tile
        frac = (idx + 0.5) / max(1, n)
        ts_sec = start_offset + frac * max(0.0, duration_sec)
        label = format_duration(ts_sec)
        draw.rectangle([x0 + 6, y0 + 6, x0 + 66, y0 + 26], fill=(15, 15, 22))
        draw.text((x0 + 12, y0 + 10), label, fill=(255, 255, 255))

    sheet.save(out_path, "JPEG", quality=82, optimize=True)
    return str(out_path)


def fetch_media_bundle(
    url: str,
    mode: str = "auto",
    start_time: str | int | float | None = None,
    end_time: str | int | float | None = None,
) -> dict[str, Any]:
    """Download lightweight video (<=max_height, <=max_size_mb MP4) or keyframes + subtitles."""
    cfg = load_settings()
    duration_threshold = int(cfg["max_duration_seconds"])
    max_height = int(cfg["max_height"])
    max_size_mb = int(cfg["max_size_mb"])
    max_video_bytes = max_size_mb * 1024 * 1024
    keyframe_count = int(cfg["keyframe_count"])

    env_default_mode = os.environ.get("AGY_VIDEO_DEFAULT_MODE", "").strip()
    configured_default = (
        env_default_mode
        if env_default_mode in ("auto", "video", "frames_and_text")
        else cfg["default_mode"]
    )
    effective_mode_input = configured_default if mode in ("auto", "", None) else mode
    resolved_mode = "video" if effective_mode_input in ("auto", "video", "", None) else effective_mode_input
    if resolved_mode not in ("video", "frames_and_text"):
        return {
            "status": "error",
            "error": f"Invalid mode {mode!r}. Use 'auto', 'video', or 'frames_and_text'.",
        }

    meta = inspect_video(url)
    if meta.get("status") != "ok":
        return meta

    total_duration = meta.get("duration_seconds")
    try:
        start_sec = parse_time_to_seconds(start_time)
        end_sec = parse_time_to_seconds(end_time)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "metadata": meta}

    has_slice = start_sec is not None or end_sec is not None
    if has_slice:
        start_sec = 0.0 if start_sec is None else start_sec
        if end_sec is None:
            if total_duration is not None:
                end_sec = min(float(total_duration), start_sec + duration_threshold)
            else:
                end_sec = start_sec + duration_threshold
        if end_sec <= start_sec:
            return {
                "status": "error",
                "error": f"end_time ({end_sec}s) must be greater than start_time ({start_sec}s).",
                "metadata": meta,
            }
        effective_duration = end_sec - start_sec
        if effective_duration > duration_threshold:
            return {
                "status": "duration_exceeded",
                "message": (
                    f"Requested slice ({format_duration(effective_duration)}) exceeds the "
                    f"{duration_threshold}s limit. Please narrow start_time/end_time or adjust "
                    "max_duration_seconds in settings."
                ),
                "metadata": meta,
            }
    else:
        effective_duration = float(total_duration) if total_duration is not None else 60.0
        if total_duration is not None and float(total_duration) > duration_threshold:
            return {
                "status": "duration_exceeded",
                "message": (
                    f"Video duration is {format_duration(total_duration)} (> {duration_threshold}s). "
                    "Full download is refused to keep context lightweight. "
                    "Please specify start_time and end_time (e.g. start_time='00:00', end_time='02:00'), "
                    "inspect comments/subtitles first, or adjust max_duration_seconds in settings."
                ),
                "metadata": meta,
            }

    ensure_cache_dir()
    canonical = meta.get("webpage_url") or url
    key_raw = (
        f"{canonical}|{resolved_mode}|{start_sec}|{end_sec}|"
        f"h{max_height}|mb{max_size_mb}|kf{keyframe_count}"
    )
    key_hash = hashlib.sha1(key_raw.encode("utf-8")).hexdigest()[:12]
    vid_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(meta.get("id") or "video"))[:32]
    entry_dir = ensure_cache_dir() / f"{vid_id}_{resolved_mode}_{key_hash}"
    entry_dir.mkdir(parents=True, exist_ok=True)
    touch_cache_entry(entry_dir)

    final_mp4 = entry_dir / "video.mp4"
    frames_dir = entry_dir / "frames"

    def _run_lru() -> None:
        evict_lru_cache(
            max_entries=int(cfg["cache_max_entries"]),
            max_age_hours=float(cfg["cache_max_age_hours"]),
            max_total_bytes=int(cfg["cache_max_mb"]) * 1024 * 1024,
            exclude=entry_dir,
        )

    # Check cache hit
    if resolved_mode == "video" and final_mp4.is_file() and final_mp4.stat().st_size <= max_video_bytes:
        sub_text, sub_path = _pick_best_subtitle(entry_dir)
        _run_lru()
        return {
            "status": "ok",
            "cache_hit": True,
            "mode": resolved_mode,
            "slice": {"start_seconds": start_sec, "end_seconds": end_sec} if has_slice else None,
            "metadata": meta,
            "video_path": str(final_mp4),
            "video_size_mb": round(final_mp4.stat().st_size / (1024 * 1024), 2),
            "keyframe_paths": [],
            "subtitle_path": sub_path,
            "subtitle_text": sub_text,
            "agent_hint": f"Call read_file on {final_mp4} to watch the video directly.",
        }

    if resolved_mode == "frames_and_text" and frames_dir.is_dir():
        existing_frames = sorted(str(p) for p in frames_dir.glob("frame_*.jpg"))
        if existing_frames:
            storyboard = entry_dir / "storyboard.jpg"
            storyboard_path = (
                str(storyboard)
                if storyboard.is_file()
                else _build_storyboard(
                    existing_frames,
                    storyboard,
                    duration_sec=effective_duration,
                    start_offset=float(start_sec or 0.0),
                )
            )
            sub_text, sub_path = _pick_best_subtitle(entry_dir)
            _run_lru()
            return {
                "status": "ok",
                "cache_hit": True,
                "mode": resolved_mode,
                "slice": {"start_seconds": start_sec, "end_seconds": end_sec} if has_slice else None,
                "metadata": meta,
                "video_path": None,
                "video_size_mb": None,
                "keyframe_paths": existing_frames,
                "storyboard_path": storyboard_path,
                "subtitle_path": sub_path,
                "subtitle_text": sub_text,
                "agent_hint": "Inspect storyboard_path (single grid image) or keyframe_paths and subtitle_text to understand the video.",
            }

    # Download lightweight <=max_height video + subtitles
    ydl_opts = _base_ydl_opts(url)
    ydl_opts.update(
        {
            "format": (
                f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
                f"b[height<={max_height}][ext=mp4]/"
                f"bv*[height<={max_height}]+ba/"
                f"b[height<={max_height}]/"
                f"best[height<={max_height}]/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": str(entry_dir / "raw.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(SUBTITLE_LANG_PRIORITY),
            "subtitlesformat": "vtt/srt/best",
            "ignoreerrors": False,
        }
    )

    if has_slice:
        ydl_opts["download_ranges"] = yt_dlp.utils.download_range_func(
            None, [(start_sec, end_sec)]
        )
        ydl_opts["force_keyframes_at_cuts"] = True

    try:
        with contextlib.redirect_stdout(sys.stderr):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Failed to download video: {exc}",
            "metadata": meta,
        }

    # Find downloaded media file
    media_candidates = [
        p
        for p in entry_dir.glob("raw.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".flv", ".mov", ".m4v"}
    ]
    if not media_candidates:
        return {
            "status": "error",
            "error": "yt-dlp finished without producing a video file.",
            "metadata": meta,
        }
    raw_video = media_candidates[0]
    sub_text, sub_path = _pick_best_subtitle(entry_dir)

    if resolved_mode == "video":
        needs_compress = (
            raw_video.suffix.lower() != ".mp4"
            or raw_video.stat().st_size > max_video_bytes
        )
        if needs_compress:
            try:
                _compress_to_mp4(
                    raw_video,
                    final_mp4,
                    duration_hint=effective_duration,
                    max_height=max_height,
                    max_video_bytes=max_video_bytes,
                )
                raw_video.unlink(missing_ok=True)
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"Video downloaded ({round(raw_video.stat().st_size / 1048576, 2)}MB) but compression failed: {exc}",
                    "metadata": meta,
                }
        else:
            raw_video.replace(final_mp4)

        size_mb = round(final_mp4.stat().st_size / (1024 * 1024), 2)
        _run_lru()
        return {
            "status": "ok",
            "cache_hit": False,
            "mode": resolved_mode,
            "slice": {"start_seconds": start_sec, "end_seconds": end_sec} if has_slice else None,
            "metadata": meta,
            "video_path": str(final_mp4),
            "video_size_mb": size_mb,
            "keyframe_paths": [],
            "subtitle_path": sub_path,
            "subtitle_text": sub_text,
            "agent_hint": f"Call read_file on {final_mp4} to watch the video directly.",
        }

    # frames_and_text mode
    try:
        keyframes = _extract_keyframes(
            raw_video,
            frames_dir,
            duration_hint=effective_duration,
            num_frames=keyframe_count,
        )
        storyboard_path = _build_storyboard(
            keyframes,
            entry_dir / "storyboard.jpg",
            duration_sec=effective_duration,
            start_offset=float(start_sec or 0.0),
        )
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Keyframe extraction failed: {exc}",
            "metadata": meta,
        }
    finally:
        raw_video.unlink(missing_ok=True)

    _run_lru()
    return {
        "status": "ok",
        "cache_hit": False,
        "mode": resolved_mode,
        "slice": {"start_seconds": start_sec, "end_seconds": end_sec} if has_slice else None,
        "metadata": meta,
        "video_path": None,
        "video_size_mb": None,
        "keyframe_paths": keyframes,
        "storyboard_path": storyboard_path,
        "subtitle_path": sub_path,
        "subtitle_text": sub_text,
        "agent_hint": "Inspect storyboard_path (single grid image) or keyframe_paths and subtitle_text to understand the video.",
    }


def fetch_generic_comments(url: str, limit: int = 30) -> dict[str, Any]:
    """Fetch top comments via yt-dlp for non-Bilibili platforms with graceful degradation."""
    limit = max(1, min(limit, 50))
    opts = _base_ydl_opts(url)
    opts.update(
        {
            "skip_download": True,
            "getcomments": True,
            "extractor_args": {
                "youtube": {"max_comments": [str(limit), str(limit), "0", "0"]},
            },
        }
    )

    try:
        with contextlib.redirect_stdout(sys.stderr):
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return {
            "platform": "generic",
            "status": "degraded",
            "warnings": [f"Generic comment extraction failed: {exc}"],
            "comment_count": 0,
            "comments": [],
            "danmaku_summary": None,
        }

    raw_comments = (info or {}).get("comments") or []
    sorted_comments = sorted(
        raw_comments,
        key=lambda c: int(c.get("like_count") or 0),
        reverse=True,
    )[:limit]

    comments_out = [
        {
            "author": c.get("author") or "unknown",
            "likes": int(c.get("like_count") or 0),
            "content": str(c.get("text") or "").strip()[:500],
        }
        for c in sorted_comments
        if str(c.get("text") or "").strip()
    ]

    return {
        "platform": (info or {}).get("extractor_key") or "generic",
        "status": "ok" if comments_out else "degraded",
        "warnings": [] if comments_out else ["No comments returned by extractor."],
        "comment_count": len(comments_out),
        "comments": comments_out,
        "danmaku_summary": None,
    }
