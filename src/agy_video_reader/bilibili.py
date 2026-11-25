"""Bilibili comment & high-energy danmaku fetcher with graceful degradation."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from .cache import COOKIES_FILE

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AVID_RE = re.compile(r"(?:^|[/?&])av(\d+)", re.IGNORECASE)
BILI_HOST_RE = re.compile(r"(bilibili\.com|b23\.tv)", re.IGNORECASE)


def is_bilibili_url(url: str) -> bool:
    return bool(BILI_HOST_RE.search(url))


def load_netscape_cookies(cookie_path: Path = COOKIES_FILE) -> dict[str, str]:
    """Parse a Netscape-format cookies.txt for bilibili.com cookies."""
    cookies: dict[str, str] = {}
    if not cookie_path.is_file():
        return cookies
    try:
        for line in cookie_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, _flag, _path, _secure, _exp, name, value = parts[:7]
                if "bilibili.com" in domain:
                    cookies[name] = value
    except OSError:
        pass
    return cookies


async def resolve_bilibili_ids(url: str) -> tuple[str | None, int | None, str]:
    """Resolve BV id or av id from a Bilibili URL (following b23.tv redirects)."""
    final_url = url
    if "b23.tv" in url.lower():
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=10.0,
                trust_env=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get(url)
                final_url = str(resp.url)
        except Exception:
            pass

    m_bv = BVID_RE.search(final_url)
    if m_bv:
        return m_bv.group(1), None, final_url

    m_av = AVID_RE.search(final_url)
    if m_av:
        return None, int(m_av.group(1)), final_url

    return None, None, final_url


def _format_ts(seconds: float) -> str:
    sec = max(0, int(seconds))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _summarize_danmaku(danmakus: list[Any], bucket_seconds: int = 15) -> dict[str, Any]:
    """Summarize total danmaku count, high-energy timestamps, and frequent phrases."""
    if not danmakus:
        return {
            "total_count": 0,
            "high_energy_moments": [],
            "top_phrases": [],
        }

    buckets: dict[int, list[str]] = defaultdict(list)
    phrase_counter: Counter[str] = Counter()

    for dm in danmakus:
        text = (getattr(dm, "text", "") or "").strip()
        dm_time = float(getattr(dm, "dm_time", 0.0) or 0.0)
        if not text:
            continue
        bucket_idx = int(dm_time // bucket_seconds)
        buckets[bucket_idx].append(text)
        normalized = text[:30]
        phrase_counter[normalized] += 1

    # Rank time windows by danmaku density
    ranked_buckets = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    high_energy = []
    for b_idx, texts in ranked_buckets:
        start_s = b_idx * bucket_seconds
        end_s = start_s + bucket_seconds
        sample_counter = Counter(texts)
        samples = [t for t, _ in sample_counter.most_common(4)]
        high_energy.append(
            {
                "time_range": f"{_format_ts(start_s)}-{_format_ts(end_s)}",
                "count": len(texts),
                "sample_danmaku": samples,
            }
        )

    top_phrases = [
        {"text": text, "count": cnt}
        for text, cnt in phrase_counter.most_common(12)
    ]

    return {
        "total_count": len(danmakus),
        "high_energy_moments": high_energy,
        "top_phrases": top_phrases,
    }


async def fetch_bilibili_comments_and_danmaku(
    url: str,
    limit: int = 30,
) -> dict[str, Any]:
    """Fetch top-liked Bilibili comments and high-energy danmaku summary.

    Always degrades gracefully on anti-bot/login requirements without bypassing
    CAPTCHAs or paywalls.
    """
    limit = max(1, min(limit, 50))
    bvid, aid, resolved_url = await resolve_bilibili_ids(url)
    if not bvid and not aid:
        return {
            "platform": "bilibili",
            "status": "degraded",
            "warning": "Could not extract BV/av ID from URL.",
            "comments": [],
            "danmaku_summary": None,
        }

    warnings: list[str] = []
    comments_out: list[dict[str, Any]] = []
    danmaku_summary: dict[str, Any] | None = None

    try:
        from bilibili_api import Credential, comment, request_settings, video

        # Direct connection for Bilibili avoids failures caused by stale shell proxy vars
        request_settings.set_proxy("")
        request_settings.set_trust_env(False)
    except Exception as exc:
        return {
            "platform": "bilibili",
            "status": "degraded",
            "warning": f"bilibili-api-python import failed: {exc}",
            "comments": [],
            "danmaku_summary": None,
        }

    cookies = load_netscape_cookies()
    cred = None
    if cookies.get("SESSDATA") or cookies.get("buvid3"):
        cred = Credential(
            sessdata=cookies.get("SESSDATA"),
            bili_jct=cookies.get("bili_jct"),
            buvid3=cookies.get("buvid3"),
            dedeuserid=cookies.get("DedeUserID"),
            ac_time_value=cookies.get("ac_time_value"),
        )

    v = video.Video(bvid=bvid, aid=aid, credential=cred)

    # Resolve aid if only bvid was provided
    if aid is None:
        try:
            info = await v.get_info()
            aid = int(info["aid"])
            bvid = info.get("bvid", bvid)
        except Exception as exc:
            warnings.append(f"Failed to fetch Bilibili video info for comments: {exc}")

    # 1. Fetch hot comments
    if aid is not None:
        try:
            raw = await comment.get_comments_lazy(
                oid=aid,
                type_=comment.CommentResourceType.VIDEO,
                order=comment.OrderType.LIKE,
                credential=cred,
            )
            replies = (raw or {}).get("replies") or []
            top_replies = ((raw or {}).get("top") or {}).get("upper")
            if isinstance(top_replies, dict):
                replies = [top_replies] + list(replies)

            seen_rpids: set[Any] = set()
            for item in replies:
                rpid = item.get("rpid")
                if rpid in seen_rpids:
                    continue
                seen_rpids.add(rpid)
                member = item.get("member") or {}
                content = (item.get("content") or {}).get("message", "").strip()
                if not content:
                    continue
                comments_out.append(
                    {
                        "author": member.get("uname", "unknown"),
                        "likes": int(item.get("like", 0) or 0),
                        "reply_count": int(item.get("rcount", 0) or 0),
                        "content": content[:500],
                    }
                )
                if len(comments_out) >= limit:
                    break
        except Exception as exc:
            hint = (
                " Bilibili may require login cookies for hot comments; "
                "you can optionally place a Netscape-format cookies.txt in the plugin directory."
                if not cookies
                else ""
            )
            warnings.append(f"Bilibili comment fetch degraded ({type(exc).__name__}: {exc}).{hint}")

    # 2. Fetch danmaku
    try:
        danmakus = await v.get_danmakus(page_index=0)
        danmaku_summary = _summarize_danmaku(danmakus or [])
    except Exception as exc:
        warnings.append(f"Bilibili danmaku fetch degraded ({type(exc).__name__}: {exc}).")

    status = "ok"
    if not comments_out and not danmaku_summary:
        status = "degraded"
    elif warnings:
        status = "partial"

    return {
        "platform": "bilibili",
        "bvid": bvid,
        "aid": aid,
        "resolved_url": resolved_url,
        "used_cookies": bool(cookies),
        "status": status,
        "warnings": warnings,
        "comment_count": len(comments_out),
        "comments": comments_out,
        "danmaku_summary": danmaku_summary,
    }
