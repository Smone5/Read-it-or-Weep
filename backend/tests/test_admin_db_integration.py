"""Real PostgreSQL integration tests for the admin backend.

Runs real migrations (001..015), real SQL queries, JSONB updates, transactions, and FK cascades.
Skipped automatically unless TEST_DATABASE_URL is set so normal unit test runs stay fast and offline.

Usage:
    TEST_DATABASE_URL=postgresql://... pytest tests/test_admin_db_integration.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import secrets
from contextlib import asynccontextmanager

import httpx
import pytest

import db
import main
import security
from config import config
from ingest import pipeline
from routers import admin as admin_router
from services import host_samples, settings_store
from storage import Storage

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="Set TEST_DATABASE_URL to a dedicated test PostgreSQL database to run DB integration tests.",
)


class _MemStore(Storage):
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def get_sync(self, key: str) -> bytes:
        return self.files[key]

    def delete_prefix_sync(self, prefix: str) -> None:
        for k in [k for k in self.files if k.startswith(prefix)]:
            self.files.pop(k, None)


@pytest.fixture
async def live_admin(monkeypatch):
    """Connect to TEST_DATABASE_URL, run migrations, create an isolated admin, and clean up afterwards."""
    monkeypatch.setattr(db, "config", dataclasses.replace(config, database_url=TEST_DB_URL))
    store = _MemStore()
    monkeypatch.setattr(admin_router, "storage", lambda: store)
    monkeypatch.setattr(host_samples, "warm_soon", lambda: None)
    monkeypatch.setattr(pipeline, "start_processing", lambda _did: None)
    monkeypatch.setattr(pipeline, "cancel_document", lambda _did: False)
    settings_store._cache.clear()

    await db.init_pool()
    await db.run_migrations()

    email = f"inttest-{secrets.token_hex(6)}@example.com"
    admin = await db.fetchrow(
        "insert into admin_users (email, name, password_hash) values ($1, 'IntTest', $2) returning id, email, name",
        email,
        security.hash_password("integration-test-pass"),
    )
    created_quiz_ids: list[str] = []
    try:
        yield {
            "admin": admin,
            "headers": {"Authorization": f"Bearer {security.issue_admin_token(admin['id'])}"},
            "quiz_ids": created_quiz_ids,
        }
    finally:
        for qid in created_quiz_ids:
            await db.execute("delete from quizzes where id = $1", qid)
        await db.execute("delete from admin_users where id = $1", admin["id"])
        await db.close_pool()


@asynccontextmanager
async def _client():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_real_sql_quiz_access_rotation_manual_unfile_and_reset_results(live_admin):
    headers = live_admin["headers"]
    async with _client() as client:
        # 1. Create a quiz and verify real SQL insert + detail query.
        c = await client.post("/api/admin/quizzes", headers=headers, json={"title": "Integration Test Quiz"})
        assert c.status_code == 200
        quiz = c.json()["quiz"]
        qid = quiz["id"]
        live_admin["quiz_ids"].append(qid)

        # 2. Seed a document, section, and image to verify FK cascades & cross-doc picture pulls.
        doc = await db.fetchrow(
            "insert into documents (quiz_id, filename, storage_key, status) values ($1, 'r.docx', 'k/r.docx', 'ready') returning id",
            qid,
        )
        sec = await db.fetchrow(
            "insert into doc_sections (document_id, quiz_id, ordinal, heading) values ($1, $2, 1, '1. Overview') returning id",
            doc["id"],
            qid,
        )
        img = await db.fetchrow(
            "insert into doc_images (document_id, quiz_id, section_id, ordinal, storage_key, usable)"
            " values ($1, $2, $3, 1, 'k/i.png', true) returning id",
            doc["id"],
            qid,
            sec["id"],
        )

        # 3. Create 3 manual MCQs (one filed under section, one using the image, one standalone).
        q_ids = []
        for idx, extra in enumerate([
            {"sectionId": sec["id"]},
            {"imageId": img["id"]},
            {},
        ]):
            r = await client.post(
                f"/api/admin/quizzes/{qid}/questions",
                headers=headers,
                json={
                    "kind": "mcq",
                    "text": f"Integration question number {idx + 1}?",
                    "options": ["Alpha", "Beta", "Gamma", "Delta"],
                    "correctIndex": 0,
                    **extra,
                },
            )
            assert r.status_code == 200
            q_ids.append(r.json()["id"])

        # 4. Unfile Q1 (sectionId: null) -> must detach document_id so deleting the document preserves it!
        u = await client.patch(f"/api/admin/questions/{q_ids[0]}", headers=headers, json={"sectionId": None})
        assert u.status_code == 200

        # 5. Delete the document -> Q1 survives, Q2 (which used doc's image) moves to pending with lost-picture reason!
        del_doc = await client.delete(f"/api/admin/documents/{doc['id']}", headers=headers)
        assert del_doc.status_code == 200

        q1_after = await db.fetchrow("select status, document_id, section_id from questions where id = $1", q_ids[0])
        assert q1_after is not None and q1_after["document_id"] is None and q1_after["status"] == "approved"

        q2_after = await db.fetchrow("select status, image_id, quality from questions where id = $1", q_ids[1])
        assert q2_after is not None and q2_after["status"] == "pending" and q2_after["image_id"] is None
        assert "picture" in (q2_after["quality"].get("pulled") or "").lower()

        # 6. Simulate a play-test flag auto-pull on Q3, then reset-results:
        # Q3 should restore to approved, while Q2's lost-picture pull stays pending!
        await db.execute(
            "update questions set status = 'pending', flag_count = 2, quality = '{\"pulled\":\"Flagged by players\"}'::jsonb where id = $1",
            q_ids[2],
        )
        rr = await client.post(f"/api/admin/quizzes/{qid}/reset-results", headers=headers)
        assert rr.status_code == 200

        q2_reset = await db.fetchrow("select status from questions where id = $1", q_ids[1])
        q3_reset = await db.fetchrow("select status, flag_count, quality from questions where id = $1", q_ids[2])
        assert q2_reset["status"] == "pending"
        assert q3_reset["status"] == "approved" and q3_reset["flag_count"] == 0 and "pulled" not in q3_reset["quality"]

        # 7. Access-code rotation bumps access_version and deletes quiz_memberships atomically.
        player = await db.fetchrow(
            "insert into players (name, email) values ('Tester', $1) returning id",
            f"player-{secrets.token_hex(6)}@example.com",
        )
        try:
            await db.execute("insert into quiz_memberships (quiz_id, player_id) values ($1, $2)", qid, player["id"])
            rot = await client.patch(f"/api/admin/quizzes/{qid}", headers=headers, json={"accessCode": "NEWCODE"})
            assert rot.status_code == 200
            ver = await db.fetchval("select access_version from quizzes where id = $1", qid)
            mem = await db.fetchval("select count(*) from quiz_memberships where quiz_id = $1", qid)
            assert ver == 2 and mem == 0
        finally:
            await db.execute("delete from players where id = $1", player["id"])


async def test_real_sql_bulk_actions_section_videos_and_open_guard(live_admin):
    headers = live_admin["headers"]
    async with _client() as client:
        c = await client.post("/api/admin/quizzes", headers=headers, json={"title": "Bulk & Video Integration"})
        assert c.status_code == 200
        qid = c.json()["quiz"]["id"]
        live_admin["quiz_ids"].append(qid)

        doc = await db.fetchrow(
            "insert into documents (quiz_id, filename, storage_key, status) values ($1, 'v.docx', 'k/v.docx', 'ready') returning id",
            qid,
        )
        sec = await db.fetchrow(
            "insert into doc_sections (document_id, quiz_id, ordinal, heading) values ($1, $2, 1, '1. Clips') returning id",
            doc["id"],
            qid,
        )

        # Create 3 pending MCQs in the section.
        q_ids = []
        for idx in range(3):
            r = await client.post(
                f"/api/admin/quizzes/{qid}/questions",
                headers=headers,
                json={
                    "kind": "mcq",
                    "text": f"Pending integration question {idx + 1}?",
                    "options": ["One", "Two", "Three", "Four"],
                    "correctIndex": 0,
                    "sectionId": sec["id"],
                    "status": "pending",
                },
            )
            assert r.status_code == 200
            q_ids.append(r.json()["id"])

        # Quiz detail reports 0 live questions and 3 total_questions for the section.
        detail = (await client.get(f"/api/admin/quizzes/{qid}", headers=headers)).json()
        assert detail["sections"][0]["questions"] == 0
        assert detail["sections"][0]["total_questions"] == 3

        # Cannot open while MCQs are still pending -> 409.
        assert (await client.patch(f"/api/admin/quizzes/{qid}", headers=headers, json={"status": "open"})).status_code == 409

        # Attach a section YouTube clip (add mode) and verify JSONB video_clips aggregation in quiz_detail.
        vid = await client.post(
            f"/api/admin/sections/{sec['id']}/video",
            headers=headers,
            json={"videoUrl": "https://youtu.be/V_F6byM7Xyo?t=12", "videoTitle": "Demo Clip", "maxQuestions": 2, "mode": "add"},
        )
        assert vid.status_code == 200 and vid.json()["updated"] == 2
        detail2 = (await client.get(f"/api/admin/quizzes/{qid}", headers=headers)).json()
        assert detail2["sections"][0]["videos"] == 2
        assert detail2["sections"][0]["video_clips"][0]["count"] == 2

        # Bulk set_difficulty + bulk approve -> now quiz can be opened!
        bd = await client.post(
            f"/api/admin/quizzes/{qid}/questions/bulk",
            headers=headers,
            json={"ids": q_ids, "action": "set_difficulty", "difficulty": "hard"},
        )
        assert bd.status_code == 200 and bd.json()["changed"] == 3

        ba = await client.post(
            f"/api/admin/quizzes/{qid}/questions/bulk",
            headers=headers,
            json={"ids": q_ids, "action": "approve"},
        )
        assert ba.status_code == 200 and ba.json()["changed"] == 3

        opened = await client.patch(f"/api/admin/quizzes/{qid}", headers=headers, json={"status": "open"})
        assert opened.status_code == 200 and opened.json()["quiz"]["status"] == "open"

        # Real-Postgres concurrency test: racing two POST /documents/{id}/reprocess calls -> exactly one wins!
        r1, r2 = await asyncio.gather(
            client.post(f"/api/admin/documents/{doc['id']}/reprocess", headers=headers),
            client.post(f"/api/admin/documents/{doc['id']}/reprocess", headers=headers),
        )
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]

        # Real-Postgres audit log test: destructive actions recorded and queryable via GET /api/admin/audit-log.
        del_b = await client.post(
            f"/api/admin/quizzes/{qid}/questions/bulk",
            headers=headers,
            json={"ids": [q_ids[0]], "action": "delete"},
        )
        assert del_b.status_code == 200 and del_b.json()["changed"] == 1
        audit_resp = await client.get(f"/api/admin/audit-log?quizId={qid}", headers=headers)
        assert audit_resp.status_code == 200
        actions = [e["action"] for e in audit_resp.json()["entries"]]
        assert "bulk_delete_questions" in actions

        # Duplicate quiz + resetSettings + clearFlags on 1-flag live question in real Postgres!
        await db.execute(
            "update questions set flag_count = 1 where id = $1",
            q_ids[1],
        )
        cf_resp = await client.patch(f"/api/admin/questions/{q_ids[1]}", headers=headers, json={"clearFlags": True})
        assert cf_resp.status_code == 200
        assert await db.fetchval("select flag_count from questions where id = $1", q_ids[1]) == 0

        dup = await client.post(
            f"/api/admin/quizzes/{qid}/duplicate",
            headers=headers,
            json={"includeManualQuestions": True},
        )
        assert dup.status_code == 200
        cloned_id = dup.json()["quiz"]["id"]
        live_admin["quiz_ids"].append(cloned_id)
        assert dup.json()["quiz"]["status"] == "draft"
        assert dup.json()["copiedQuestions"] == 2

        # Override quiz settings then reset to company defaults.
        ov = await client.patch(f"/api/admin/quizzes/{cloned_id}", headers=headers, json={"settings": {"ready_threshold": 55}})
        assert ov.status_code == 200 and ov.json()["quiz"]["settings"]["ready_threshold"] == 55
        rs = await client.patch(f"/api/admin/quizzes/{cloned_id}", headers=headers, json={"resetSettings": True})
        assert rs.status_code == 200 and rs.json()["quiz"]["settings"] == {}


