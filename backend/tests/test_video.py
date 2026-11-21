"""Tests for legal YouTube video URL parsing and validation."""
from __future__ import annotations

import pytest
from services import video


def test_parses_standard_short_embed_and_timestamps():
    v1 = video.parse_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m25s", "Highlight")
    assert v1 is not None
    assert v1["videoId"] == "dQw4w9WgXcQ"
    assert v1["startSec"] == 85
    assert v1["watchUrl"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=85s"
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in v1["embedUrl"]
    assert "start=85" in v1["embedUrl"]
    assert v1["title"] == "Highlight"

    v2 = video.parse_youtube("https://youtu.be/dQw4w9WgXcQ?t=42")
    assert v2 is not None and v2["startSec"] == 42 and v2["embedUrl"] is not None

    ext = video.parse_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ#external", "NFL Clip")
    assert ext is not None
    assert ext["embedUrl"] is None
    assert ext["watchUrl"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_rejects_non_youtube_or_malformed_urls():
    assert video.parse_youtube("https://evil.example/watch?v=dQw4w9WgXcQ") is None
    assert video.parse_youtube("javascript:alert(1)") is None
    assert video.parse_youtube("https://www.youtube.com/watch?v=short") is None
    assert video.normalize_url("   ") is None
    with pytest.raises(ValueError):
        video.normalize_url("https://vimeo.com/123456")
