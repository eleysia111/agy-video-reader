"""Persistent user settings for agy-video-reader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cache import PLUGIN_ROOT

SETTINGS_FILE = PLUGIN_ROOT / "settings.json"

ALLOWED_HEIGHTS = (360, 480, 720, 1080)
ALLOWED_MODES = ("auto", "video", "frames_and_text")

DEFAULT_SETTINGS: dict[str, Any] = {
    "max_duration_seconds": 300,
    "max_height": 480,
    "max_size_mb": 25,
    "default_mode": "auto",
    "keyframe_count": 8,
    "default_comment_limit": 30,
    "cache_max_entries": 12,
    "cache_max_mb": 300,
    "cache_max_age_hours": 24,
}


def _clamp_int(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, iv))


def sanitize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge partial settings with defaults and enforce safe bounds."""
    src = raw if isinstance(raw, dict) else {}
    merged = dict(DEFAULT_SETTINGS)

    merged["max_duration_seconds"] = _clamp_int(
        src.get("max_duration_seconds", merged["max_duration_seconds"]),
        30,
        1800,
        DEFAULT_SETTINGS["max_duration_seconds"],
    )

    h_raw = _clamp_int(
        src.get("max_height", merged["max_height"]),
        360,
        1080,
        DEFAULT_SETTINGS["max_height"],
    )
    # Snap to nearest allowed height
    merged["max_height"] = min(ALLOWED_HEIGHTS, key=lambda x: abs(x - h_raw))

    merged["max_size_mb"] = _clamp_int(
        src.get("max_size_mb", merged["max_size_mb"]),
        5,
        100,
        DEFAULT_SETTINGS["max_size_mb"],
    )

    mode = str(src.get("default_mode", merged["default_mode"])).strip()
    merged["default_mode"] = mode if mode in ALLOWED_MODES else DEFAULT_SETTINGS["default_mode"]

    merged["keyframe_count"] = _clamp_int(
        src.get("keyframe_count", merged["keyframe_count"]),
        4,
        20,
        DEFAULT_SETTINGS["keyframe_count"],
    )

    merged["default_comment_limit"] = _clamp_int(
        src.get("default_comment_limit", merged["default_comment_limit"]),
        5,
        50,
        DEFAULT_SETTINGS["default_comment_limit"],
    )

    merged["cache_max_entries"] = _clamp_int(
        src.get("cache_max_entries", merged["cache_max_entries"]),
        2,
        100,
        DEFAULT_SETTINGS["cache_max_entries"],
    )

    merged["cache_max_mb"] = _clamp_int(
        src.get("cache_max_mb", merged["cache_max_mb"]),
        50,
        2048,
        DEFAULT_SETTINGS["cache_max_mb"],
    )

    merged["cache_max_age_hours"] = _clamp_int(
        src.get("cache_max_age_hours", merged["cache_max_age_hours"]),
        1,
        168,
        DEFAULT_SETTINGS["cache_max_age_hours"],
    )

    return merged


def load_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    """Load persisted settings from disk, falling back to defaults."""
    if not path.is_file():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sanitize_settings(data)
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(updates: dict[str, Any], path: Path = SETTINGS_FILE) -> dict[str, Any]:
    """Merge updates into existing settings, validate, persist to disk, and return result."""
    current = load_settings(path)
    current.update({k: v for k, v in updates.items() if v is not None})
    cleaned = sanitize_settings(current)
    path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cleaned


def reset_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    """Reset settings to defaults and persist."""
    cleaned = dict(DEFAULT_SETTINGS)
    path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cleaned
