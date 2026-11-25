"""FastMCP / MCPServer stdio server for agy-video-reader with MCP App settings UI."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

# Keep stderr clean for MCP stdio clients
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from mcp.types import ImageContent, TextContent

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # pragma: no cover - fallback for mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .bilibili import fetch_bilibili_comments_and_danmaku, is_bilibili_url
from .cache import COOKIES_FILE, cleanup_all_cache, evict_lru_cache, get_cache_stats
from .media import fetch_generic_comments, fetch_media_bundle, find_ffmpeg, inspect_video
from .settings import load_settings, reset_settings, save_settings

SETTINGS_UI_URI = "ui://agy-video-reader/settings.html"
SETTINGS_UI_MIME = "text/html;profile=mcp-app"
SETTINGS_HTML_PATH = Path(__file__).resolve().parent / "ui" / "settings_panel.html"

SETTINGS_PANE_META = {
    "ui": {"resourceUri": SETTINGS_UI_URI},
    "com.google.antigravity/ui": {
        "entrypoints": [
            {"type": "aux_pane", "title": "🎬 视频读取设置"},
        ]
    },
}

mcp = FastMCP(
    "agy-video-reader",
    instructions=(
        "Tools for inspecting web video links, downloading lightweight MP4 videos "
        "or keyframes+subtitles, fetching hot comments and Bilibili danmaku summaries, "
        "managing local cache, and adjusting duration/resolution/size settings. "
        "Never attempt to bypass paywalls or CAPTCHAs."
    ),
)


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _settings_payload() -> dict[str, Any]:
    return {
        "settings": load_settings(),
        "cache": get_cache_stats(),
        "ffmpeg_available": bool(find_ffmpeg()),
        "used_cookies": COOKIES_FILE.is_file(),
    }


@mcp.resource(
    SETTINGS_UI_URI,
    name="AGY Video Reader Settings",
    mime_type=SETTINGS_UI_MIME,
    meta={"ui": {"prefersBorder": False}},
)
def settings_panel_html() -> str:
    """Interactive MCP App panel for tuning video duration, resolution, size, and cache."""
    return SETTINGS_HTML_PATH.read_text(encoding="utf-8")


@mcp.tool(title="视频读取设置面板", meta=SETTINGS_PANE_META)
async def open_settings(include_cache_stats: bool = True) -> dict[str, Any]:
    """Open the interactive settings panel (Antigravity / DSH MCP Apps) and return current settings.

    Note: Keeping an optional parameter avoids empty-object tool schemas that some
    DSH/Gemini API gateways reject with 400 errors.

    Args:
        include_cache_stats: Include local cache directory usage stats (default True).
    """
    def _load() -> dict[str, Any]:
        payload = _settings_payload()
        if not include_cache_stats:
            payload.pop("cache", None)
        return payload

    return await asyncio.to_thread(_load)


@mcp.tool()
async def update_settings(
    max_duration_seconds: int | None = None,
    max_height: int | None = None,
    max_size_mb: int | None = None,
    default_mode: str | None = None,
    keyframe_count: int | None = None,
    default_comment_limit: int | None = None,
    cache_max_entries: int | None = None,
    cache_max_mb: int | None = None,
    cache_max_age_hours: int | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Update and persist agy-video-reader settings (takes effect immediately).

    Args:
        max_duration_seconds: Max allowed video/slice duration in seconds (30..1800, default 300).
        max_height: Max video height in pixels (360, 480, 720, or 1080; default 480).
        max_size_mb: Max MP4 file size in MB before ffmpeg compression (5..100, default 25).
        default_mode: Default fetch mode: 'auto', 'video', or 'frames_and_text'.
        keyframe_count: Number of keyframes extracted in frames_and_text mode (4..20, default 8).
        default_comment_limit: Default number of hot comments to fetch (5..50, default 30).
        cache_max_entries: Max LRU cache entries (2..100, default 12).
        cache_max_mb: Max LRU cache size in MB (50..2048, default 300).
        cache_max_age_hours: Max LRU cache retention in hours (1..168, default 24).
        reset: If True, resets all settings back to defaults.
    """
    def _apply() -> dict[str, Any]:
        if reset:
            reset_settings()
        else:
            save_settings(
                {
                    "max_duration_seconds": max_duration_seconds,
                    "max_height": max_height,
                    "max_size_mb": max_size_mb,
                    "default_mode": default_mode,
                    "keyframe_count": keyframe_count,
                    "default_comment_limit": default_comment_limit,
                    "cache_max_entries": cache_max_entries,
                    "cache_max_mb": cache_max_mb,
                    "cache_max_age_hours": cache_max_age_hours,
                }
            )
        return _settings_payload()

    return await asyncio.to_thread(_apply)


@mcp.tool()
async def inspect_video_link(url: str) -> str:
    """Quickly inspect a web video link (Bilibili, YouTube, etc.) without downloading media.

    Returns title, uploader, duration, description, available subtitles, whether it exceeds
    the configured duration threshold, and recommended next steps.
    """
    result = await asyncio.to_thread(inspect_video, url.strip())
    return _dump(result)


@mcp.tool()
async def fetch_comments(url: str, limit: int | None = None) -> str:
    """Fetch top-liked hot comments and (for Bilibili) high-energy danmaku summary.

    Supports optional cookies.txt in the plugin root directory. Always degrades gracefully
    on anti-bot/login limits without crashing or attempting to bypass CAPTCHAs.
    """
    clean_url = url.strip()
    effective_limit = int(limit) if limit is not None else int(load_settings()["default_comment_limit"])
    if is_bilibili_url(clean_url):
        result = await fetch_bilibili_comments_and_danmaku(clean_url, limit=effective_limit)
    else:
        result = await asyncio.to_thread(fetch_generic_comments, clean_url, effective_limit)
    return _dump(result)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@mcp.tool()
async def fetch_video_bundle(
    url: str,
    mode: str = "auto",
    start_time: str | float | None = None,
    end_time: str | float | None = None,
    inline_storyboard: bool | None = None,
) -> list[TextContent | ImageContent]:
    """Download a lightweight video bundle (<=max_height, <=max_size_mb MP4) or keyframes + storyboard + subtitles,
    along with hot comments / danmaku summary.

    Args:
        url: Web video URL (e.g., Bilibili, YouTube).
        mode: 'auto' (uses AGY_VIDEO_DEFAULT_MODE env var or configured default_mode),
              'video' (lightweight MP4 for video-capable agents like Antigravity via read_file),
              or 'frames_and_text' (evenly spaced JPEG keyframes + single timestamped storyboard + subtitles,
              ideal for DSH / DeepSeek Harness and other non-video agents).
        start_time: Optional slice start time (seconds or MM:SS / HH:MM:SS). Required when
                    video duration exceeds max_duration_seconds.
        end_time: Optional slice end time (seconds or MM:SS / HH:MM:SS). Single slice must
                  be <= max_duration_seconds.
        inline_storyboard: When True (or AGY_VIDEO_INLINE_STORYBOARD=1) in frames_and_text mode,
                           returns the timestamped storyboard JPEG inline as MCP ImageContent.
    """
    clean_url = url.strip()
    media_result = await asyncio.to_thread(
        fetch_media_bundle, clean_url, mode, start_time, end_time
    )

    if media_result.get("status") in ("ok", "duration_exceeded"):
        try:
            comment_limit = min(15, int(load_settings()["default_comment_limit"]))
            if is_bilibili_url(clean_url):
                comments_result = await fetch_bilibili_comments_and_danmaku(
                    clean_url, limit=comment_limit
                )
            else:
                comments_result = await asyncio.to_thread(
                    fetch_generic_comments, clean_url, comment_limit
                )
        except Exception as exc:
            comments_result = {
                "status": "degraded",
                "warnings": [f"Comment fetch skipped due to error: {exc}"],
                "comment_count": 0,
                "comments": [],
                "danmaku_summary": None,
            }
        media_result["comments_summary"] = comments_result

    blocks: list[TextContent | ImageContent] = [
        TextContent(type="text", text=_dump(media_result))
    ]

    want_inline = (
        inline_storyboard
        if inline_storyboard is not None
        else _env_truthy("AGY_VIDEO_INLINE_STORYBOARD")
    )
    sb_path_str = media_result.get("storyboard_path")
    if want_inline and sb_path_str:
        sb_path = Path(sb_path_str)
        if sb_path.is_file():
            try:
                raw_bytes = await asyncio.to_thread(sb_path.read_bytes)
                b64 = base64.b64encode(raw_bytes).decode("ascii")
                blocks.append(
                    ImageContent(type="image", data=b64, mimeType="image/jpeg")
                )
            except Exception:
                pass

    return blocks


@mcp.tool()
async def cleanup_cache(max_age_hours: float | None = None) -> dict[str, Any]:
    """Clean up downloaded video/keyframe cache files.

    Args:
        max_age_hours: If omitted, clears the entire cache directory. If provided,
                       only removes entries older than max_age_hours.
    """
    return await asyncio.to_thread(cleanup_all_cache, max_age_hours)


def main() -> None:
    try:
        cfg = load_settings()
        evict_lru_cache(
            max_entries=int(cfg["cache_max_entries"]),
            max_age_hours=float(cfg["cache_max_age_hours"]),
            max_total_bytes=int(cfg["cache_max_mb"]) * 1024 * 1024,
        )
    except Exception:
        pass
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
