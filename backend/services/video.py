"""Legal YouTube video clip links & privacy-enhanced embeds for question reveals.

Never downloads or re-hosts third-party video. Accepts only official YouTube URLs,
extracts the 11-character video ID and optional start timestamp, and builds both:
  * ``watchUrl`` — direct link to YouTube (works for every video, including NFL clips
    where the channel restricts third-party iframe embeds), and
  * ``embedUrl`` — privacy-enhanced ``youtube-nocookie.com`` iframe URL when inline
    playback is permitted.

Append ``#external`` (or pass ``external_only=True``) when the rights holder
disables third-party embedding so the player skips the iframe and opens YouTube cleanly.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

_YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HMS_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$", re.I)

# Matches YouTube URLs inside raw document text (paragraphs, slides, PDFs, markdown).
_TEXT_YT_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s<>\"')]+|(?:shorts|embed|live)/[A-Za-z0-9_-]{11}[^\s<>\"')]*)"
    r"|youtu\.be/[A-Za-z0-9_-]{11}[^\s<>\"')]*)",
    re.I,
)

# Channels well-known to disable third-party iframe embeds on highlight clips.
_EXTERNAL_ONLY_AUTHORS = {
    "nfl", "nfl throwback", "nfl films", "denver broncos", "tampa bay buccaneers",
    "nba", "mlb", "nhl", "espn",
}


def _parse_start(raw: str | None) -> int:
    if not raw:
        return 0
    raw = raw.strip()
    if raw.isdigit():
        return max(0, int(raw))
    m = _HMS_RE.match(raw)
    if not m or not any(m.groups()):
        return 0
    h, mins, s = (int(g or 0) for g in m.groups())
    return max(0, h * 3600 + mins * 60 + s)


def parse_youtube(url: str | None, title: str | None = None) -> dict[str, Any] | None:
    """Return a frontend-safe video clip descriptor, or None if not a valid YouTube URL."""
    if not url or not isinstance(url, str):
        return None
    raw = url.strip().rstrip(".,;:")
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None

    qs = parse_qs(parsed.query)
    vid: str | None = None
    path_parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("youtu.be"):
        vid = path_parts[0] if path_parts else None
    elif path_parts and path_parts[0] in ("embed", "shorts", "live"):
        vid = path_parts[1] if len(path_parts) > 1 else None
    elif "v" in qs:
        vid = qs["v"][0]

    if not vid or not _VIDEO_ID_RE.match(vid):
        return None

    start = _parse_start((qs.get("t") or qs.get("start") or [None])[0])
    external_only = (parsed.fragment.lower() == "external") or ((qs.get("noembed") or [""])[0] in ("1", "true"))

    watch_url = f"https://www.youtube.com/watch?v={vid}" + (f"&t={start}s" if start else "")
    embed_url = (
        None if external_only
        else f"https://www.youtube-nocookie.com/embed/{vid}?rel=0&autoplay=1&playsinline=1&enablejsapi=1" + (f"&start={start}" if start else "")
    )
    return {
        "provider": "youtube",
        "videoId": vid,
        "watchUrl": watch_url,
        "embedUrl": embed_url,
        "thumbnailUrl": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "startSec": start,
        "externalOnly": external_only,
        "title": (title or "").strip() or "Watch video clip",
    }


def normalize_url(url: str | None, *, external_only: bool | None = None) -> str | None:
    """Validate and normalize a YouTube URL for storage (returns None for empty input, raises ValueError if invalid)."""
    if url is None or not url.strip():
        return None
    info = parse_youtube(url)
    if info is None:
        raise ValueError("Please enter a valid YouTube link (youtube.com/watch?v=... or youtu.be/...).")
    use_external = info["externalOnly"] if external_only is None else bool(external_only)
    return info["watchUrl"] + ("#external" if use_external else "")


def extract_youtube_urls(text: str | None, extra_links: list[str] | None = None) -> list[str]:
    """Extract unique normalized YouTube URLs from document text and hyperlink targets, preserving order."""
    candidates: list[str] = []
    if extra_links:
        candidates.extend(extra_links)
    if text:
        candidates.extend(_TEXT_YT_RE.findall(text))
    seen: set[str] = set()
    found: list[str] = []
    for raw in candidates:
        info = parse_youtube(raw)
        if info and info["videoId"] not in seen:
            seen.add(info["videoId"])
            suffix = "#external" if info["externalOnly"] else ""
            found.append(info["watchUrl"] + suffix)
    return found


async def inspect_youtube(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    """Validate a YouTube URL and query YouTube's public oEmbed endpoint for title, channel & embed status."""
    info = parse_youtube(url)
    if info is None:
        raise ValueError("Please enter a valid YouTube link (youtube.com/watch?v=... or youtu.be/...).")

    author: str | None = None
    yt_title: str | None = None
    embeddable = True
    suggested_external = bool(info["externalOnly"])
    note: str | None = None

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": info["watchUrl"], "format": "json"},
            )
        if resp.status_code == 200 and len(resp.content) <= 65_536:
            meta = resp.json()
            yt_title = (meta.get("title") or "").strip() or None
            author = (meta.get("author_name") or "").strip() or None
            if author and author.lower() in _EXTERNAL_ONLY_AUTHORS:
                embeddable = False
                suggested_external = True
                note = f"{author} typically restricts third-party embeds — recommended to open on YouTube."
        elif resp.status_code in (401, 403):
            embeddable = False
            suggested_external = True
            note = "The uploader disabled third-party embedding — players will open this clip on YouTube."
        elif resp.status_code == 404:
            raise ValueError("YouTube could not find that video. Double-check the link.")
    except ValueError:
        raise
    except Exception:
        # Offline / transient network issue: return parsed preview cleanly without failing the Admin.
        note = "Could not reach YouTube right now to look up the title — you can still enter a title manually."

    return {
        "videoId": info["videoId"],
        "watchUrl": info["watchUrl"],
        "thumbnailUrl": info["thumbnailUrl"],
        "startSec": info["startSec"],
        "title": yt_title,
        "author": author,
        "embeddable": embeddable,
        "suggestedExternal": suggested_external,
        "note": note,
        "normalizedUrl": info["watchUrl"] + ("#external" if suggested_external else ""),
    }
