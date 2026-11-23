"""Upload and parser fuzzing tests for the admin backend.

Verifies that malformed Office archives, zip bombs, corrupt PDFs, adversarial SVGs,
oversized payloads, and tricky filenames always fail safely with clean 4xx/UnreadableDocument errors.
"""
from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager

import httpx
import pytest

import db
import main
import security
from config import config
from ingest import UnreadableDocument, parse_document
from ingest.docx_parser import check_ooxml_zip
from routers import admin as admin_router, play as play_router
from services import settings_store
from storage import Storage

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
QUIZ_ID = "33333333-3333-3333-3333-333333333333"
DOC_ID = "44444444-4444-4444-4444-444444444444"


class _MemStore(Storage):
    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (data, content_type)

    def get_sync(self, key: str) -> bytes:
        return self.files[key][0]

    def delete_prefix_sync(self, prefix: str) -> None:
        for k in [k for k in self.files if k.startswith(prefix)]:
            self.files.pop(k, None)


def _zip_bytes(entries: dict[str, bytes], *, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compress) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.parametrize(
    "filename,payload",
    [
        ("corrupt.docx", b"not a zip file at all"),
        ("truncated.docx", b"PK\x03\x04\x14\x00\x00\x00"),
        ("empty_zip.docx", _zip_bytes({})),
        ("broken_xml.docx", _zip_bytes({"[Content_Types].xml": b"<broken", "word/document.xml": b"<w:document><unclosed"})),
        ("corrupt.pptx", b"not a presentation"),
        ("empty_zip.pptx", _zip_bytes({})),
        ("corrupt.pdf", b"%PDF-1.7\nthis is truncated garbage"),
        ("binary_not_pdf.pdf", b"\x00\xff\x01\x02" * 64),
        ("whitespace.txt", b"   \n\t\r\n   "),
        ("empty.md", b""),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("script.html", b"<script>alert(1)</script>"),
    ],
)
def test_parsers_reject_corrupt_and_unsupported_documents_cleanly(filename: str, payload: bytes):
    with pytest.raises(UnreadableDocument):
        parse_document(payload, filename)


def test_ooxml_zip_bomb_guards_block_high_ratio_and_excess_entries():
    # 1) High compression ratio bomb: 12 MB of zeros compresses to ~12 KB (> 500x ratio).
    bomb = _zip_bytes({"word/document.xml": b"A" * (12 * 1024 * 1024)})
    assert len(bomb) < 100_000
    with pytest.raises(UnreadableDocument, match="compression ratio"):
        check_ooxml_zip(bomb, "Word (.docx) document")
    with pytest.raises(UnreadableDocument, match="compression ratio"):
        parse_document(bomb, "bomb.docx")
    with pytest.raises(UnreadableDocument, match="compression ratio"):
        parse_document(bomb, "bomb.pptx")

    # 2) Excess archive entry count (> 2500 tiny entries).
    many = _zip_bytes({f"entry_{i}.xml": b"<a/>" for i in range(2505)}, compress=zipfile.ZIP_STORED)
    with pytest.raises(UnreadableDocument, match="too many archive entries"):
        check_ooxml_zip(many, "Word (.docx) document")


@pytest.mark.parametrize(
    "svg_payload",
    [
        b"<svg><script>alert(1)</script></svg>",
        b"<svg><SCRIPT>alert(1)</SCRIPT></svg>",
        b"<svg>< script>alert(1)</script></svg>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><g onmouseover = "alert(1)"/></svg>',
        b'<svg><a xlink:href="javascript:alert(1)"><rect/></a></svg>',
        b'<svg><a href="JaVaScRiPt:alert(1)"><rect/></a></svg>',
        b'<svg><image href="data:image/svg+xml;base64,PHN2Zz4="/></svg>',
        b'<svg><use href="https://evil.example/x.svg#p"/></svg>',
        b'<svg><use href="//evil.example/x.svg#p"/></svg>',
        b"<svg><foreignObject><body>hi</body></foreignObject></svg>",
        b'<svg><set attributeName="href" to="javascript:alert(1)"/></svg>',
        b'<svg><animate attributeName="href" values="javascript:alert(1)"/></svg>',
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
        b"<svg>&#x6a;avascript:alert(1)</svg>",
        b"<svg><style>@import url(https://evil.example/a.css);</style></svg>",
        b"<svg>\x00<rect/></svg>",
    ],
)
async def test_logo_upload_rejects_adversarial_svgs(svg_payload: bytes, monkeypatch):
    store = _MemStore()
    monkeypatch.setattr(admin_router, "storage", lambda: store)
    play_router._hits.clear()
    settings_store._cache.clear()

    async def fake_fetchrow(sql, *args):
        if "from admin_users" in sql.lower():
            return {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}
        return None

    monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
    headers = {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"}

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/admin/settings/branding/logo",
            headers=headers,
            files={"file": ("logo.svg", svg_payload, "image/svg+xml")},
        )
    assert resp.status_code == 415
    assert store.files == {}


@pytest.mark.parametrize(
    "raw_name,expected_suffix",
    [
        ("../../../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\windows\\system32\\notes.md", "notes.md"),
        ("Q3\x00Report\r\n<script>.txt", ".txt"),
        ("a" * 300 + ".txt", ""),  # truncated safely to <= 160 chars; tested separately below
    ],
)
async def test_document_upload_sanitizes_hostile_filenames(raw_name: str, expected_suffix: str, monkeypatch):
    store = _MemStore()
    monkeypatch.setattr(admin_router, "storage", lambda: store)
    monkeypatch.setattr(type(config), "ai_configured", property(lambda _self: True))
    from ingest import pipeline

    monkeypatch.setattr(pipeline, "start_processing", lambda _did: None)

    async def fake_fetchrow(sql, *args):
        low = sql.lower()
        if "from admin_users" in low:
            return {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}
        if "from quizzes where id" in low:
            return {"id": QUIZ_ID, "slug": "q", "title": "Q"}
        if "insert into documents" in low:
            return {"id": DOC_ID}
        return None

    async def fake_fetchval(_sql, *_args):
        return None

    async def fake_fetch(_sql, *_args):
        return []

    async def fake_execute(_sql, *_args):
        return "UPDATE 1"

    monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(db, "fetchval", fake_fetchval)
    monkeypatch.setattr(db, "fetch", fake_fetch)
    monkeypatch.setattr(db, "execute", fake_execute)

    headers = {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/documents",
            headers=headers,
            files={"file": (raw_name, b"Valid report text for testing.", "text/plain")},
        )
    if raw_name.startswith("a" * 200):
        # Truncated before the .txt suffix -> extension disappears -> rejected cleanly as 415.
        assert resp.status_code == 415
    else:
        assert resp.status_code == 200
        stored_key = next(iter(store.files))
        assert ".." not in stored_key and "\x00" not in stored_key and "\\" not in stored_key
        assert stored_key.endswith(expected_suffix)
