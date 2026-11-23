"""Concurrency and cancellation tests for admin background ingestion and locking paths."""
from __future__ import annotations

import asyncio

import httpx
import pytest

import db
import main
import security
from ingest import pipeline
from routers import admin as admin_router, play as play_router
from services import bank
from storage import Storage

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
QUIZ_ID = "33333333-3333-3333-3333-333333333333"
DOC_ID = "44444444-4444-4444-4444-444444444444"


class _MemStore(Storage):
    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        pass

    def get_sync(self, key: str) -> bytes:
        return b""

    def delete_prefix_sync(self, prefix: str) -> None:
        pass


async def test_concurrent_reprocess_only_one_request_wins_claim(monkeypatch):
    """Two simultaneous POST /documents/{id}/reprocess requests must only start processing once."""
    play_router._hits.clear()
    lock = asyncio.Lock()
    doc_state = {"status": "ready", "stage": None, "storage_key": "quizzes/reports/doc/r.docx"}
    started: list[str] = []

    async def fake_fetchrow(sql, *args):
        low = sql.lower()
        if "from admin_users" in low:
            return {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}
        if "update documents set status = 'processing'" in low:
            async with lock:
                # Yield inside the critical check so both requests truly overlap on the event loop.
                await asyncio.sleep(0.01)
                if doc_state["status"] != "processing" and doc_state["stage"] is None and doc_state["storage_key"]:
                    doc_state["status"] = "processing"
                    doc_state["stage"] = "reading"
                    return {"quiz_id": QUIZ_ID}
                return None
        if "select status, storage_key from documents" in low:
            return {"status": doc_state["status"], "storage_key": doc_state["storage_key"]}
        return None

    monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(pipeline, "start_processing", lambda did: started.append(did))
    monkeypatch.setattr(bank, "invalidate", lambda _qid: None)

    headers = {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r1, r2 = await asyncio.gather(
            client.post(f"/api/admin/documents/{DOC_ID}/reprocess", headers=headers),
            client.post(f"/api/admin/documents/{DOC_ID}/reprocess", headers=headers),
        )

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409]
    assert started == [DOC_ID]


async def test_concurrent_generate_more_only_one_request_wins_claim(monkeypatch):
    """Two simultaneous POST /quizzes/{id}/questions/generate requests must only launch one AI top-up."""
    play_router._hits.clear()
    lock = asyncio.Lock()
    doc_stage: dict[str, str | None] = {"stage": None}
    started: list[str] = []

    async def fake_fetchrow(sql, *args):
        low = sql.lower()
        if "from admin_users" in low:
            return {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}
        if "from quizzes where id" in low:
            return {"id": QUIZ_ID, "slug": "q", "title": "Q"}
        return None

    async def fake_fetch(sql, *args):
        if "update documents set stage = 'writing'" in sql.lower():
            async with lock:
                await asyncio.sleep(0.01)
                if doc_stage["stage"] is None:
                    doc_stage["stage"] = "writing"
                    return [{"id": DOC_ID}]
                return []
        return []

    async def fake_fetchval(sql, *args):
        if "from documents where quiz_id = $1 and status = 'ready'" in sql.lower():
            return 1
        return None

    monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(db, "fetch", fake_fetch)
    monkeypatch.setattr(db, "fetchval", fake_fetchval)
    monkeypatch.setattr(pipeline, "start_more_questions", lambda did: started.append(did))

    headers = {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r1, r2 = await asyncio.gather(
            client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/generate", headers=headers),
            client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/generate", headers=headers),
        )

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409]
    assert started == [DOC_ID]


async def test_delete_document_cancels_in_flight_pipeline_task_cleanly(monkeypatch):
    """Deleting a document mid-ingestion must cancel the background asyncio task and remove it from _running."""
    play_router._hits.clear()
    store = _MemStore()
    monkeypatch.setattr(admin_router, "storage", lambda: store)
    monkeypatch.setattr(bank, "invalidate", lambda _qid: None)

    entered = asyncio.Event()
    was_cancelled = asyncio.Event()

    async def slow_process(document_id: str) -> None:
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            was_cancelled.set()
            raise

    monkeypatch.setattr(pipeline, "process_document", slow_process)

    async def fake_fetchrow(sql, *args):
        low = sql.lower()
        if "from admin_users" in low:
            return {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}
        if "select quiz_id, filename from documents" in low:
            return {"quiz_id": QUIZ_ID, "filename": "report.docx"}
        return None

    async def fake_execute(_sql, *_args):
        return "DELETE 1"

    monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(db, "execute", fake_execute)

    pipeline.start_processing(DOC_ID)
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    assert DOC_ID in pipeline._tasks_by_doc

    headers = {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"}
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.delete(f"/api/admin/documents/{DOC_ID}", headers=headers)

    assert resp.status_code == 200
    await asyncio.wait_for(was_cancelled.wait(), timeout=2.0)
    await asyncio.sleep(0.05)  # let done_callback prune pipeline._tasks_by_doc / _running
    assert DOC_ID not in pipeline._tasks_by_doc
