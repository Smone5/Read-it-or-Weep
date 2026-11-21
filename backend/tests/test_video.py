"""Tests for legal YouTube video URL parsing, extraction from documents, and normalization."""
from __future__ import annotations

import io
import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from ingest import parse_document
from services import video


def test_parses_standard_short_embed_and_timestamps():
    v1 = video.parse_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m25s", "Highlight")
    assert v1 is not None
    assert v1["videoId"] == "dQw4w9WgXcQ"
    assert v1["startSec"] == 85
    assert v1["watchUrl"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=85s"
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in v1["embedUrl"]
    assert "start=85" in v1["embedUrl"]
    assert v1["externalOnly"] is False
    assert v1["title"] == "Highlight"

    v2 = video.parse_youtube("https://youtu.be/dQw4w9WgXcQ?t=42")
    assert v2 is not None and v2["startSec"] == 42 and v2["embedUrl"] is not None

    ext = video.parse_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ#external", "NFL Clip")
    assert ext is not None
    assert ext["embedUrl"] is None
    assert ext["externalOnly"] is True
    assert ext["watchUrl"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_rejects_non_youtube_or_malformed_urls_and_toggles_external():
    assert video.parse_youtube("https://evil.example/watch?v=dQw4w9WgXcQ") is None
    assert video.parse_youtube("javascript:alert(1)") is None
    assert video.parse_youtube("https://www.youtube.com/watch?v=short") is None
    assert video.normalize_url("   ") is None
    assert video.normalize_url("https://youtu.be/dQw4w9WgXcQ", external_only=True) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ#external"
    assert video.normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ#external", external_only=False) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        video.normalize_url("https://vimeo.com/123456")


def test_extracts_unique_youtube_urls_from_text_and_links():
    found = video.extract_youtube_urls(
        "Watch https://youtu.be/t-Sm4kTUGCc?t=30 and ignore https://example.com/foo and duplicate https://www.youtube.com/watch?v=t-Sm4kTUGCc.",
        extra_links=["https://www.youtube.com/watch?v=V_F6byM7Xyo", "https://example.org/docs"],
    )
    assert found == [
        "https://www.youtube.com/watch?v=V_F6byM7Xyo",
        "https://www.youtube.com/watch?v=t-Sm4kTUGCc&t=30s",
    ]


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    rel_id = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def test_docx_parser_extracts_both_hyperlinked_and_pasted_youtube_urls():
    doc = Document()
    doc.add_heading("1. Stadium Arrival and Parking", level=1)
    p1 = doc.add_paragraph(
        "Standard parking lots open four and a half hours before kickoff at Empower Field at Mile High. "
        "Early tailgaters head to Lot C six hours before kickoff and pay with card or digital wallet. "
        "RTD light rail riders arrive at the Empower Field station on the E and W lines and walk straight to the west gates. "
    )
    _add_hyperlink(p1, "Watch the official stadium tour", "https://www.youtube.com/watch?v=V_F6byM7Xyo")

    doc.add_heading("2. Championship Moments", level=1)
    doc.add_paragraph(
        "John Elway powered Denver to back-to-back titles in Super Bowls XXXII and XXXIII alongside MVP Terrell Davis, "
        "who rushed for more than two thousand yards in 1998. Eighteen years later, Von Miller and the No Fly Zone "
        "carried Denver to a twenty-four to ten victory over Carolina in Super Bowl 50. "
        "Replay the Helicopter dive here: https://youtu.be/BuL1QaVXUpM?t=15"
    )

    buf = io.BytesIO()
    doc.save(buf)
    structured = parse_document(buf.getvalue(), "staff_guide.docx")
    assert len(structured.sections) == 2
    assert structured.sections[0].video_urls == ["https://www.youtube.com/watch?v=V_F6byM7Xyo"]
    assert structured.sections[1].video_urls == ["https://www.youtube.com/watch?v=BuL1QaVXUpM&t=15s"]


def test_csp_allows_youtube_thumbnails_and_embeds():
    import main
    assert "frame-src 'self' https://www.youtube-nocookie.com https://www.youtube.com" in main._CSP
    assert "https://i.ytimg.com" in main._CSP
    assert "frame-ancestors 'none'" in main._CSP
