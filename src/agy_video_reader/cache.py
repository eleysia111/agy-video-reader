"""Cache directory management with automatic LRU eviction."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PLUGIN_ROOT / "cache"
COOKIES_FILE = PLUGIN_ROOT / "cookies.txt"

# LRU eviction defaults
MAX_CACHE_ENTRIES = 12
MAX_CACHE_AGE_HOURS = 24.0
MAX_CACHE_TOTAL_BYTES = 300 * 1024 * 1024  # 300 MB


def ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    for root, _dirs, files in os.walk(path):
        for fname in files:
            fpath = Path(root) / fname
            try:
                total += fpath.stat().st_size
            except OSError:
                pass
    return total


def _entry_mtime(path: Path) -> float:
    try:
        latest = path.stat().st_mtime
    except OSError:
        return 0.0
    if path.is_dir():
        for root, _dirs, files in os.walk(path):
            for fname in files:
                try:
                    t = (Path(root) / fname).stat().st_mtime
                    if t > latest:
                        latest = t
                except OSError:
                    pass
    return latest


def touch_cache_entry(entry_dir: Path) -> None:
    """Update mtime so LRU recognizes recent access."""
    try:
        os.utime(entry_dir, None)
    except OSError:
        pass


def evict_lru_cache(
    max_entries: int = MAX_CACHE_ENTRIES,
    max_age_hours: float = MAX_CACHE_AGE_HOURS,
    max_total_bytes: int = MAX_CACHE_TOTAL_BYTES,
    exclude: Path | None = None,
) -> dict[str, Any]:
    """Automatically evict expired or least-recently-used cache entries."""
    ensure_cache_dir()
    now = time.time()
    max_age_sec = max_age_hours * 3600.0
    exclude_resolved = exclude.resolve() if exclude else None

    entries: list[tuple[Path, float, int]] = []
    for item in CACHE_DIR.iterdir():
        if exclude_resolved and item.resolve() == exclude_resolved:
            continue
        entries.append((item, _entry_mtime(item), _dir_size(item)))

    # Sort oldest first
    entries.sort(key=lambda x: x[1])

    removed_count = 0
    removed_bytes = 0
    kept: list[tuple[Path, float, int]] = []

    # 1. Remove expired entries
    for item, mtime, size in entries:
        if max_age_sec > 0 and (now - mtime) > max_age_sec:
            if _remove_path(item):
                removed_count += 1
                removed_bytes += size
        else:
            kept.append((item, mtime, size))

    # 2. Enforce max entry count and max total bytes (oldest first)
    total_bytes = sum(x[2] for x in kept)
    while kept and (len(kept) > max_entries or total_bytes > max_total_bytes):
        item, _mtime, size = kept.pop(0)
        if _remove_path(item):
            removed_count += 1
            removed_bytes += size
            total_bytes -= size

    return {
        "evicted_entries": removed_count,
        "evicted_bytes": removed_bytes,
        "remaining_entries": len(kept),
        "remaining_bytes": total_bytes,
    }


def get_cache_stats() -> dict[str, Any]:
    """Return current cache directory usage stats."""
    ensure_cache_dir()
    entries = list(CACHE_DIR.iterdir())
    total_bytes = sum(_dir_size(item) for item in entries)
    return {
        "cache_dir": str(CACHE_DIR),
        "entry_count": len(entries),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
    }


def cleanup_all_cache(max_age_hours: float | None = None) -> dict[str, Any]:
    """Clean up cache directory completely or remove entries older than max_age_hours."""
    ensure_cache_dir()
    now = time.time()
    removed_entries = 0
    removed_bytes = 0
    remaining_entries = 0
    remaining_bytes = 0

    for item in list(CACHE_DIR.iterdir()):
        size = _dir_size(item)
        mtime = _entry_mtime(item)
        should_remove = max_age_hours is None or (now - mtime) > (max_age_hours * 3600.0)
        if should_remove:
            if _remove_path(item):
                removed_entries += 1
                removed_bytes += size
            else:
                remaining_entries += 1
                remaining_bytes += size
        else:
            remaining_entries += 1
            remaining_bytes += size

    return {
        "cache_dir": str(CACHE_DIR),
        "removed_entries": removed_entries,
        "removed_bytes": removed_bytes,
        "removed_mb": round(removed_bytes / (1024 * 1024), 2),
        "remaining_entries": remaining_entries,
        "remaining_bytes": remaining_bytes,
        "remaining_mb": round(remaining_bytes / (1024 * 1024), 2),
    }


def _remove_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return not path.exists()
    except OSError:
        return False
