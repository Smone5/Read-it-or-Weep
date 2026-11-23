"""Comprehensive HTTP-level tests for the admin backend (/api/admin/*).

Exercises the real FastAPI app (middleware, SecurityHeaders, JWT auth, Pydantic validation,
UUID path parsing, multipart uploads, rate limiting, and error mapping) against a
deterministic in-memory DB/storage harness so the entire suite runs fast without Postgres.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt
import pytest

import db
import llm
import main
import security
from config import config
from ingest import pipeline
from routers import admin as admin_router, play as play_router
from services import bank, eleven, host_samples, host_service, live, music_service, settings_store, stats, video
from storage import Storage


# --------------------------------------------------------------------------- test doubles

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ADMIN_ID = "22222222-2222-2222-2222-222222222222"
QUIZ_ID = "33333333-3333-3333-3333-333333333333"
DOC_ID = "44444444-4444-4444-4444-444444444444"
SECTION_ID = "55555555-5555-5555-5555-555555555555"
IMAGE_ID = "66666666-6666-6666-6666-666666666666"
REVEAL_IMG_ID = "77777777-7777-7777-7777-777777777777"
QUESTION_ID = "88888888-8888-8888-8888-888888888888"
SESSION_ID = "99999999-9999-9999-9999-999999999999"
MISSING_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

ADMIN_ROW = {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss"}

VALID_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
VALID_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="10"/></svg>'


class MemoryStorage(Storage):
    def __init__(self, *, fail_put: bool = False):
        self.files: dict[str, tuple[bytes, str]] = {}
        self.deleted_prefixes: list[str] = []
        self.fail_put = fail_put

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise OSError("disk full")
        self.files[key] = (data, content_type)

    def get_sync(self, key: str) -> bytes:
        return self.files[key][0]

    def delete_prefix_sync(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)
        for k in [k for k in self.files if k.startswith(prefix)]:
            self.files.pop(k, None)


class FakeDB:
    """Minimal SQL-dispatching fake for `db` + `db.transaction()` that records every call."""

    def __init__(self, *, admin_present: bool = True):
        self.admin_present = admin_present
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.tx_count = 0
        self.handlers: list[tuple[str, str, Any]] = []

    def on(self, op: str, needle: str, result: Any) -> None:
        self.handlers.append((op, needle.lower(), result))

    def _resolve(self, op: str, sql: str, args: tuple[Any, ...]) -> Any:
        self.calls.append((op, sql, args))
        low = sql.lower()
        for h_op, needle, result in reversed(self.handlers):
            if (h_op == op or h_op == "*") and needle in low:
                return result(sql, args) if callable(result) else result
        if op == "fetchrow" and "from admin_users where id = $1" in low:
            if self.admin_present and args and args[0] == ADMIN_ID:
                return dict(ADMIN_ROW)
            return None
        if op == "execute":
            return "UPDATE 1"
        if op == "fetch":
            return []
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self._resolve("fetch", sql, args)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return self._resolve("fetchrow", sql, args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._resolve("fetchval", sql, args)

    async def execute(self, sql: str, *args: Any) -> str:
        return self._resolve("execute", sql, args)

    @asynccontextmanager
    async def transaction(self):
        self.tx_count += 1
        yield self


def _sample_quiz(**overrides: Any) -> dict[str, Any]:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    base = {
        "id": QUIZ_ID,
        "slug": "q3-review",
        "title": "Q3 Business Review",
        "description": "Board packet",
        "meeting_at": None,
        "status": "draft",
        "access_code": None,
        "access_version": 1,
        "allowed_email_domains": [],
        "teams": ["Sales", "Ops"],
        "settings": {},
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _sample_question(**overrides: Any) -> dict[str, Any]:
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    base = {
        "id": QUESTION_ID,
        "quiz_id": QUIZ_ID,
        "document_id": DOC_ID,
        "section_id": SECTION_ID,
        "image_id": None,
        "reveal_image_id": None,
        "kind": "mcq",
        "source_type": "text",
        "skill": "recall",
        "title": "Top Line",
        "question_text": "What was Q3 company revenue?",
        "options": ["$184M", "$140M", "$210M", "$95M"],
        "correct_index": 0,
        "is_true": None,
        "explanation": "Revenue hit $184M.",
        "source_quote": "Revenue hit $184M.",
        "video_url": None,
        "video_title": None,
        "difficulty": "medium",
        "status": "approved",
        "origin": "manual",
        "pinned": False,
        "edited_at": None,
        "flag_count": 0,
        "times_served": 4,
        "times_correct": 3,
        "quality": {},
        "heading": "1. Financials",
        "created_at": now,
    }
    base.update(overrides)
    return base


@pytest.fixture
def env(monkeypatch):
    """Patch DB, storage, caches, throttles, and background triggers cleanly per test."""
    fake_db = FakeDB()
    mem_store = MemoryStorage()

    for name in ("fetch", "fetchrow", "fetchval", "execute", "transaction"):
        monkeypatch.setattr(db, name, getattr(fake_db, name))
    monkeypatch.setattr(admin_router, "storage", lambda: mem_store)
    play_router._hits.clear()
    settings_store._cache.clear()
    bank._cache.clear()
    monkeypatch.setattr(host_samples, "warm_soon", lambda: None)

    invalidated: list[str] = []
    monkeypatch.setattr(bank, "invalidate", lambda qid: invalidated.append(qid))

    return {
        "db": fake_db,
        "storage": mem_store,
        "invalidated": invalidated,
        "headers": {"Authorization": f"Bearer {security.issue_admin_token(ADMIN_ID)}"},
    }


@asynccontextmanager
async def _client():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# --------------------------------------------------------------------------- 1. Auth & security matrix


async def test_all_protected_admin_routes_reject_missing_invalid_player_expired_and_deleted_tokens(env):
    """Every admin route except /login must reject unauthenticated, player, expired, and deleted-admin JWTs."""
    player_token = security.issue_player_token("player-999")
    expired_token = jwt.encode(
        {"sub": ADMIN_ID, "typ": "admin", "iat": int(time.time()) - 7200, "exp": int(time.time()) - 60},
        config.app_secret,
        algorithm="HS256",
    )
    deleted_admin_token = security.issue_admin_token(MISSING_UUID)

    sample_paths: list[tuple[str, str]] = []
    for route in admin_router.router.routes:
        path = route.path
        if path == "/api/admin/login":
            continue
        concrete = (
            path.replace("{quiz_id}", QUIZ_ID)
            .replace("{document_id}", DOC_ID)
            .replace("{section_id}", SECTION_ID)
            .replace("{question_id}", QUESTION_ID)
            .replace("{session_id}", SESSION_ID)
            .replace("{admin_id}", OTHER_ADMIN_ID)
            .replace("{key}", "branding")
        )
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            sample_paths.append((method, concrete))

    assert len(sample_paths) >= 34, "expected full admin route surface"

    async with _client() as client:
        for method, path in sample_paths:
            for label, hdrs in (
                ("missing", {}),
                ("malformed", {"Authorization": "Bearer not-a-jwt"}),
                ("player-jwt", {"Authorization": f"Bearer {player_token}"}),
                ("expired", {"Authorization": f"Bearer {expired_token}"}),
                ("deleted-admin", {"Authorization": f"Bearer {deleted_admin_token}"}),
            ):
                resp = await client.request(method, path, headers=hdrs)
                assert resp.status_code == 401, f"{method} {path} with {label} returned {resp.status_code}"

        # Security headers are always attached by ASGI middleware even on 401/200 admin responses.
        me_resp = await client.get("/api/admin/me", headers=env["headers"])
        assert me_resp.status_code == 200
        assert me_resp.json()["admin"]["email"] == "boss@example.com"
        assert me_resp.headers["x-frame-options"] == "DENY"
        assert me_resp.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in me_resp.headers["content-security-policy"]


# --------------------------------------------------------------------------- 2. Login & admin users


async def test_admin_login_flow_and_failed_only_throttling(env):
    pw_hash = security.hash_password("correct-horse-battery")
    env["db"].on(
        "fetchrow",
        "from admin_users where lower(email)",
        {"id": ADMIN_ID, "email": "boss@example.com", "name": "Boss", "password_hash": pw_hash},
    )

    async with _client() as client:
        # Case-insensitive login succeeds and updates last_login_at.
        ok = await client.post("/api/admin/login", json={"email": "BOSS@example.com", "password": "correct-horse-battery"})
        assert ok.status_code == 200
        data = ok.json()
        assert security.read_admin_token(data["token"]) == ADMIN_ID
        assert any("update admin_users set last_login_at" in sql.lower() for _, sql, _ in env["db"].calls)

        # Repeated valid logins do NOT trip the per-account failed-attempt lockout.
        for _ in range(11):
            play_router._hits.pop("admin-login:127.0.0.1", None)  # isolate per-account counter from IP burst counter
            r = await client.post("/api/admin/login", json={"email": "boss@example.com", "password": "correct-horse-battery"})
            assert r.status_code == 200

        # 10 wrong passwords trip the per-account lockout -> 11th returns 429 even with correct password.
        for _ in range(10):
            play_router._hits.pop("admin-login:127.0.0.1", None)
            bad = await client.post("/api/admin/login", json={"email": "boss@example.com", "password": "wrong-password"})
            assert bad.status_code == 401
        play_router._hits.pop("admin-login:127.0.0.1", None)
        locked = await client.post("/api/admin/login", json={"email": "boss@example.com", "password": "correct-horse-battery"})
        assert locked.status_code == 429


async def test_admin_users_crud_and_self_delete_guard(env):
    existing_emails = {"boss@example.com"}

    def check_exists(_sql, args):
        return 1 if args[0].lower() in existing_emails else None

    def insert_admin(_sql, args):
        email, name, pw_hash = args
        assert email == email.lower()
        assert security.verify_password(pw_hash, "super-secret-pass")
        existing_emails.add(email)
        return {"id": OTHER_ADMIN_ID, "email": email, "name": name}

    env["db"].on("fetchval", "from admin_users where lower(email)", check_exists)
    env["db"].on("fetchrow", "insert into admin_users", insert_admin)
    env["db"].on(
        "fetchrow",
        "delete from admin_users where id = $1 returning id",
        lambda _sql, args: {"id": args[0]} if args[0] == OTHER_ADMIN_ID else None,
    )

    async with _client() as client:
        # Short password rejected by Pydantic validation.
        short = await client.post("/api/admin/admins", headers=env["headers"], json={"email": "new@example.com", "password": "short"})
        assert short.status_code == 422

        # Duplicate email rejected case-insensitively.
        dup = await client.post("/api/admin/admins", headers=env["headers"], json={"email": "BOSS@example.com", "password": "super-secret-pass"})
        assert dup.status_code == 409

        # New admin created.
        created = await client.post(
            "/api/admin/admins",
            headers=env["headers"],
            json={"email": "NewAdmin@Example.com", "name": "Alex", "password": "super-secret-pass"},
        )
        assert created.status_code == 200
        assert created.json()["admin"]["email"] == "newadmin@example.com"

        # Self-delete blocked.
        self_del = await client.delete(f"/api/admin/admins/{ADMIN_ID}", headers=env["headers"])
        assert self_del.status_code == 400

        # Non-existent admin returns 404.
        missing_del = await client.delete(f"/api/admin/admins/{MISSING_UUID}", headers=env["headers"])
        assert missing_del.status_code == 404

        # Deleting another existing admin succeeds.
        ok_del = await client.delete(f"/api/admin/admins/{OTHER_ADMIN_ID}", headers=env["headers"])
        assert ok_del.status_code == 200


# --------------------------------------------------------------------------- 3. Settings, branding & logo upload


async def test_settings_read_write_strips_logo_key_and_clamps_game_rules(env):
    stored: dict[str, dict] = {}
    env["db"].on("fetchval", "select value from settings where key = $1", lambda _s, a: stored.get(a[0]))

    def save_setting(_sql, args):
        stored[args[0]] = args[1]
        return "INSERT 0 1"

    env["db"].on("execute", "insert into settings", save_setting)

    async with _client() as client:
        # Invalid key rejected.
        assert (await client.get("/api/admin/settings/unknown", headers=env["headers"])).status_code == 422

        # PUT /settings/branding rejects forged logo_key and unknown fields with 422 (extra="forbid").
        forged = await client.put(
            "/api/admin/settings/branding",
            headers=env["headers"],
            json={"company_name": "Acme Corp", "logo_key": "branding/evil.svg"},
        )
        assert forged.status_code == 422
        assert (
            await client.put(
                "/api/admin/settings/branding",
                headers=env["headers"],
                json={"totally_unknown_field": True},
            )
        ).status_code == 422

        # Valid branding update saves cleanly, resets bad hex color, and writes an audit entry.
        r = await client.put(
            "/api/admin/settings/branding",
            headers=env["headers"],
            json={"company_name": "Acme Corp", "primary_color": "not-a-color"},
        )
        assert r.status_code == 200
        assert r.json()["value"]["company_name"] == "Acme Corp"
        assert r.json()["value"]["logo_key"] is None
        assert r.json()["value"]["primary_color"] == settings_store.DEFAULTS["branding"]["primary_color"]
        assert any("insert into admin_audit_log" in sql.lower() for _, sql, _ in env["db"].calls)

        # PUT /settings/game rejects unknown fields and clamps out-of-range numbers.
        assert (
            await client.put("/api/admin/settings/game", headers=env["headers"], json={"unknown_rule": 1})
        ).status_code == 422
        g = await client.put(
            "/api/admin/settings/game",
            headers=env["headers"],
            json={"ready_threshold": 999, "show": {"questions": 1, "timer_sec": 9999}},
        )
        assert g.status_code == 200
        val = g.json()["value"]
        assert val["ready_threshold"] == 100
        assert val["show"]["questions"] == 3
        assert val["show"]["timer_sec"] == 120

        # GET /api/admin/audit-log returns audit entries (both global and quiz-filtered).
        env["db"].on(
            "fetch",
            "from admin_audit_log",
            [{"id": "a1", "admin_id": ADMIN_ID, "admin_email": "boss@example.com", "action": "update_settings",
              "target_type": "settings", "target_id": "branding", "quiz_id": None, "details": {}, "created_at": "2026-11-22T12:00:00Z"}],
        )
        al = await client.get("/api/admin/audit-log?limit=25", headers=env["headers"])
        assert al.status_code == 200 and len(al.json()["entries"]) == 1
        al_q = await client.get(f"/api/admin/audit-log?quizId={QUIZ_ID}", headers=env["headers"])
        assert al_q.status_code == 200


async def test_logo_upload_validation_magic_bytes_svg_sanitization_and_delete(env):
    stored: dict[str, dict] = {}
    env["db"].on("fetchval", "select value from settings where key = $1", lambda _s, a: stored.get(a[0]))
    env["db"].on("execute", "insert into settings (key, value) values ('branding'", lambda _s, a: stored.update({"branding": a[0]}) or "INSERT 0 1")
    env["db"].on("execute", "update settings set value = $1", lambda _s, a: stored.update({"branding": a[0]}) or "UPDATE 1")

    async with _client() as client:
        # Unsupported extension -> 415.
        r = await client.post("/api/admin/settings/branding/logo", headers=env["headers"], files={"file": ("logo.gif", b"GIF89a", "image/gif")})
        assert r.status_code == 415

        # Empty file -> 422.
        r = await client.post("/api/admin/settings/branding/logo", headers=env["headers"], files={"file": ("logo.png", b"", "image/png")})
        assert r.status_code == 422

        # Oversized (>2MB) -> 413.
        r = await client.post(
            "/api/admin/settings/branding/logo",
            headers=env["headers"],
            files={"file": ("big.png", VALID_PNG + b"\x00" * (admin_router.MAX_LOGO_BYTES + 1), "image/png")},
        )
        assert r.status_code == 413

        # Spoofed magic bytes (HTML renamed to .png) -> 415.
        r = await client.post(
            "/api/admin/settings/branding/logo",
            headers=env["headers"],
            files={"file": ("fake.png", b"<html><script>alert(1)</script></html>", "image/png")},
        )
        assert r.status_code == 415

        # Active SVG -> 415.
        r = await client.post(
            "/api/admin/settings/branding/logo",
            headers=env["headers"],
            files={"file": ("bad.svg", b'<svg onload="alert(1)"><rect/></svg>', "image/svg+xml")},
        )
        assert r.status_code == 415

        # Valid PNG uploads with server-derived MIME type (ignoring spoofed text/html header).
        r = await client.post(
            "/api/admin/settings/branding/logo",
            headers=env["headers"],
            files={"file": ("logo.png", VALID_PNG, "text/html")},
        )
        assert r.status_code == 200
        first_key = r.json()["value"]["logo_key"]
        assert first_key.startswith("branding/logo-") and first_key.endswith(".png")
        assert env["storage"].files[first_key][1] == "image/png"

        # Uploading a valid static SVG replaces the PNG and deletes the previous key.
        r2 = await client.post(
            "/api/admin/settings/branding/logo",
            headers=env["headers"],
            files={"file": ("logo.svg", VALID_SVG, "application/octet-stream")},
        )
        assert r2.status_code == 200
        second_key = r2.json()["value"]["logo_key"]
        assert env["storage"].files[second_key][1] == "image/svg+xml"
        assert first_key in env["storage"].deleted_prefixes

        # DELETE removes logo and clears logo_key.
        d = await client.delete("/api/admin/settings/branding/logo", headers=env["headers"])
        assert d.status_code == 200
        assert d.json()["value"]["logo_key"] is None
        assert second_key in env["storage"].deleted_prefixes


# --------------------------------------------------------------------------- 4. Host sample, voice usage & music


async def test_host_sample_voice_usage_and_music_routes(env, monkeypatch):
    async with _client() as client:
        # Voice usage: unconfigured -> configured -> error fallback.
        monkeypatch.setattr(eleven, "configured", lambda: False)
        assert (await client.get("/api/admin/voice-usage", headers=env["headers"])).json() == {"configured": False}

        monkeypatch.setattr(eleven, "configured", lambda: True)

        async def ok_account():
            return {"tier": "creator", "character_count": 4600, "character_limit": 27600, "next_character_count_reset_unix": 1796000000}

        monkeypatch.setattr(eleven, "account", ok_account)
        u = (await client.get("/api/admin/voice-usage", headers=env["headers"])).json()
        assert u["configured"] is True and u["gamesLeft"] == 10 and u["tier"] == "creator"

        async def fail_account():
            raise eleven.ElevenError("Quota API down")

        monkeypatch.setattr(eleven, "account", fail_account)
        u_err = (await client.get("/api/admin/voice-usage", headers=env["headers"])).json()
        assert u_err == {"configured": True, "error": "Quota API down"}

        # Host sample: invalid voice -> 422; valid voice -> base64 wav; AI error -> 502.
        bad_v = await client.post("/api/admin/host/sample", headers=env["headers"], json={"sarcasm": 5, "voice": "not-a-voice"})
        assert bad_v.status_code == 422

        async def fake_sample_audio(sarcasm, name, voice, **kwargs):
            return ("Welcome to the show!", b"RIFFWAVE")

        monkeypatch.setattr(host_service, "sample_audio", fake_sample_audio)
        ok_s = await client.post("/api/admin/host/sample", headers=env["headers"], json={"sarcasm": 5, "name": "Max", "voice": "ash"})
        assert ok_s.status_code == 200
        assert ok_s.json()["audio"].startswith("data:audio/wav;base64,")

        async def boom_sample(*args, **kwargs):
            raise RuntimeError("secret-provider-key-trace")

        monkeypatch.setattr(host_service, "sample_line", boom_sample)
        err_s = await client.post("/api/admin/host/sample", headers=env["headers"], json={"sarcasm": 5})
        assert err_s.status_code == 502
        assert "secret-provider-key-trace" not in err_s.text

        # Music status & refresh.
        env["db"].on("fetch", "from music_tracks", [{"decade": "80s", "tracks": 42}])
        m = await client.get("/api/admin/music", headers=env["headers"])
        assert m.status_code == 200
        by_dec = {d["decade"]: d["tracks"] for d in m.json()["decades"]}
        assert by_dec["80s"] == 42

        assert (await client.post("/api/admin/music/refresh", headers=env["headers"], json={"decade": "1890s"})).status_code == 422
        refreshed: list[str] = []
        monkeypatch.setattr(music_service, "ensure_decade", lambda dec: refreshed.append(dec))
        assert (await client.post("/api/admin/music/refresh", headers=env["headers"], json={"decade": "90s"})).status_code == 200
        assert refreshed == ["90s"]


# --------------------------------------------------------------------------- 5. Quizzes lifecycle & security


async def test_quiz_create_update_access_rotation_and_open_guards(env):
    quiz_state = _sample_quiz()
    mcq_counts = {"all": 2, "manual": 1, "ai": 1}

    env["db"].on("fetchrow", "select * from quizzes where id = $1", lambda _s, a: dict(quiz_state) if a[0] == QUIZ_ID else None)
    env["db"].on("fetchval", "select 1 from quizzes where slug = $1", None)

    def create_row(_sql, args):
        slug, title, desc, meeting_at, _admin = args
        return _sample_quiz(slug=slug, title=title, description=desc, meeting_at=meeting_at)

    env["db"].on("fetchrow", "insert into quizzes", create_row)

    def count_live(sql, _args):
        if "origin = 'manual'" in sql:
            return mcq_counts["manual"]
        if "origin = 'generated'" in sql:
            return mcq_counts["ai"]
        return mcq_counts["all"]

    env["db"].on("fetchval", "select count(*) from questions where quiz_id = $1", count_live)

    def update_row(sql, args):
        # Parse column assignments from `update quizzes set col1 = $2, col2 = $3, updated_at = now() where id = $1`
        set_part = sql.split("set ", 1)[1].split(", updated_at", 1)[0]
        cols = [piece.split("=")[0].strip() for piece in set_part.split(",")]
        for col, val in zip(cols, args[1:]):
            quiz_state[col] = val
        return dict(quiz_state)

    env["db"].on("fetchrow", "update quizzes set", update_row)

    async with _client() as client:
        # Whitespace-only title rejected on create and update.
        assert (await client.post("/api/admin/quizzes", headers=env["headers"], json={"title": "   "})).status_code == 422
        assert (await client.patch(f"/api/admin/quizzes/{QUIZ_ID}", headers=env["headers"], json={"title": "  "})).status_code == 422

        # Missing quiz -> 404.
        assert (await client.get(f"/api/admin/quizzes/{MISSING_UUID}", headers=env["headers"])).status_code == 404

        # Create quiz succeeds.
        created = await client.post("/api/admin/quizzes", headers=env["headers"], json={"title": "  Q4 Forecast  "})
        assert created.status_code == 200
        assert created.json()["quiz"]["title"] == "Q4 Forecast"
        assert created.json()["quiz"]["slug"] == "q4-forecast"

        # Rotating access code bumps access_version and wipes quiz_memberships inside a transaction.
        tx_before = env["db"].tx_count
        rot = await client.patch(
            f"/api/admin/quizzes/{QUIZ_ID}",
            headers=env["headers"],
            json={"accessCode": "BOARD2026"},
        )
        assert rot.status_code == 200
        assert quiz_state["access_version"] == 2
        assert env["db"].tx_count == tx_before + 1
        assert any("delete from quiz_memberships where quiz_id = $1" in sql.lower() for _, sql, _ in env["db"].calls)

        # Making a draft unlisted adds an 8-hex random suffix; download vs purge stays mutually exclusive; numbers clamped.
        patch_rules = await client.patch(
            f"/api/admin/quizzes/{QUIZ_ID}",
            headers=env["headers"],
            json={"settings": {"unlisted": True, "allow_download": True, "purge_source_after_process": True, "show": {"questions": 999}}},
        )
        assert patch_rules.status_code == 200
        assert quiz_state["slug"].startswith("q3-review-") and len(quiz_state["slug"].split("-")[-1]) == 8
        assert quiz_state["settings"]["purge_source_after_process"] is True
        assert quiz_state["settings"]["allow_download"] is False
        assert quiz_state["settings"]["show"]["questions"] == 30
        assert "live" not in quiz_state["settings"]  # inherited defaults were not flattened into quiz overrides

        # Opening with <3 approved MCQs is refused with 409.
        fail_open = await client.patch(f"/api/admin/quizzes/{QUIZ_ID}", headers=env["headers"], json={"status": "open"})
        assert fail_open.status_code == 409

        # Opening with question_sources="manual" checks manual MCQ count specifically.
        mcq_counts["all"] = 10
        mcq_counts["manual"] = 2
        fail_manual = await client.patch(
            f"/api/admin/quizzes/{QUIZ_ID}",
            headers=env["headers"],
            json={"status": "open", "settings": {"question_sources": "manual"}},
        )
        assert fail_manual.status_code == 409

        mcq_counts["manual"] = 4
        ok_open = await client.patch(
            f"/api/admin/quizzes/{QUIZ_ID}",
            headers=env["headers"],
            json={"status": "open", "settings": {"question_sources": "manual"}},
        )
        assert ok_open.status_code == 200
        assert ok_open.json()["quiz"]["status"] == "open"


async def test_quiz_delete_cancels_pipeline_and_cleans_storage(env, monkeypatch):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", _sample_quiz())
    env["db"].on("fetch", "select id from documents where quiz_id = $1", [{"id": DOC_ID}])
    cancelled: list[str] = []
    monkeypatch.setattr(pipeline, "cancel_document", lambda did: cancelled.append(did))

    async with _client() as client:
        r = await client.delete(f"/api/admin/quizzes/{QUIZ_ID}", headers=env["headers"])
        assert r.status_code == 200
        assert cancelled == [DOC_ID]
        assert f"quizzes/images/{DOC_ID}" in env["storage"].deleted_prefixes
        assert f"quizzes/reports/{DOC_ID}" in env["storage"].deleted_prefixes
        assert QUIZ_ID in env["invalidated"]


# --------------------------------------------------------------------------- 6. Documents & section videos


async def test_document_upload_validation_sanitization_and_storage_failure_cleanup(env, monkeypatch):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", lambda _s, a: _sample_quiz() if a[0] == QUIZ_ID else None)
    env["db"].on("fetchrow", "insert into documents", {"id": DOC_ID})
    started: list[str] = []
    monkeypatch.setattr(pipeline, "start_processing", lambda did: started.append(did))
    monkeypatch.setattr(type(config), "ai_configured", property(lambda _self: True))

    async with _client() as client:
        # Missing quiz -> 404 on both GET and POST.
        assert (await client.get(f"/api/admin/quizzes/{MISSING_UUID}/documents", headers=env["headers"])).status_code == 404
        assert (
            await client.post(
                f"/api/admin/quizzes/{MISSING_UUID}/documents",
                headers=env["headers"],
                files={"file": ("report.txt", b"hello", "text/plain")},
            )
        ).status_code == 404

        # Unsupported .doc -> 415 with helpful .docx hint.
        bad_ext = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/documents",
            headers=env["headers"],
            files={"file": ("legacy.doc", b"abc", "application/msword")},
        )
        assert bad_ext.status_code == 415 and ".docx" in bad_ext.json()["detail"]

        # Empty file -> 422.
        empty = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/documents",
            headers=env["headers"],
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert empty.status_code == 422

        # Valid upload sanitizes path traversal filename and starts processing.
        ok = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/documents",
            headers=env["headers"],
            files={"file": ("../../secret/Q3?Report!.txt", b"Q3 revenue was $184M.", "text/plain")},
        )
        assert ok.status_code == 200
        assert started == [DOC_ID]
        stored_keys = list(env["storage"].files.keys())
        assert len(stored_keys) == 1
        assert ".." not in stored_keys[0] and stored_keys[0].endswith("/Q3_Report_.txt")

        # Storage failure cleans up the orphan documents row instead of leaving a broken ghost doc.
        env["storage"].fail_put = True
        with pytest.raises(OSError):
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/documents",
                headers=env["headers"],
                files={"file": ("second.txt", b"payload", "text/plain")},
            )
        assert any("delete from documents where id = $1" in sql.lower() for _, sql, _ in env["db"].calls)
        env["storage"].fail_put = False

        # AI unconfigured -> 503.
        monkeypatch.setattr(type(config), "ai_configured", property(lambda _self: False))
        no_ai = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/documents",
            headers=env["headers"],
            files={"file": ("report.txt", b"hello", "text/plain")},
        )
        assert no_ai.status_code == 503


async def test_document_reprocess_and_delete_guards(env, monkeypatch):
    started: list[str] = []
    cancelled: list[str] = []
    monkeypatch.setattr(pipeline, "start_processing", lambda did: started.append(did))
    monkeypatch.setattr(pipeline, "cancel_document", lambda did: cancelled.append(did))

    async with _client() as client:
        # Missing document -> 404.
        assert (await client.post(f"/api/admin/documents/{MISSING_UUID}/reprocess", headers=env["headers"])).status_code == 404

        # Purged source file -> 409.
        env["db"].on("fetchrow", "select status, storage_key from documents", {"status": "ready", "storage_key": ""})
        purged = await client.post(f"/api/admin/documents/{DOC_ID}/reprocess", headers=env["headers"])
        assert purged.status_code == 409 and "purged" in purged.json()["detail"].lower()

        # Currently processing -> 409.
        env["db"].on("fetchrow", "select status, storage_key from documents", {"status": "processing", "storage_key": "k"})
        busy = await client.post(f"/api/admin/documents/{DOC_ID}/reprocess", headers=env["headers"])
        assert busy.status_code == 409 and "still being processed" in busy.json()["detail"].lower()

        # Successful atomic claim -> 200 + starts pipeline.
        env["db"].on("fetchrow", "update documents set status = 'processing'", {"quiz_id": QUIZ_ID})
        ok = await client.post(f"/api/admin/documents/{DOC_ID}/reprocess", headers=env["headers"])
        assert ok.status_code == 200 and started == [DOC_ID]

        # Delete document cancels pipeline, pulls cross-doc picture questions, clears reveal_image_id, deletes prefixes.
        env["db"].on("fetchrow", "select quiz_id, filename from documents where id = $1", {"quiz_id": QUIZ_ID, "filename": "report.docx"})
        d = await client.delete(f"/api/admin/documents/{DOC_ID}", headers=env["headers"])
        assert d.status_code == 200
        assert cancelled == [DOC_ID]
        assert any("set status = 'pending', image_id = null" in sql.lower() for _, sql, _ in env["db"].calls)
        assert any("set reveal_image_id = null" in sql.lower() for _, sql, _ in env["db"].calls)
        assert f"quizzes/images/{DOC_ID}" in env["storage"].deleted_prefixes
        assert f"quizzes/reports/{DOC_ID}" in env["storage"].deleted_prefixes


async def test_section_video_add_replace_remove_and_inspect(env, monkeypatch):
    env["db"].on(
        "fetchrow",
        "from doc_sections where id = $1",
        lambda _s, a: {"id": SECTION_ID, "quiz_id": QUIZ_ID, "heading": "1. Highlights"} if a[0] == SECTION_ID else None,
    )
    env["db"].on("fetch", "select id from questions where section_id", [{"id": QUESTION_ID}])

    async with _client() as client:
        # Inspect invalid URL -> 422.
        bad_v = await client.post("/api/admin/video/inspect", headers=env["headers"], json={"url": "https://example.com/not-youtube"})
        assert bad_v.status_code == 422

        # Missing section -> 404.
        assert (
            await client.post(
                f"/api/admin/sections/{MISSING_UUID}/video",
                headers=env["headers"],
                json={"videoUrl": "https://www.youtube.com/watch?v=V_F6byM7Xyo"},
            )
        ).status_code == 404

        # Invalid YouTube URL -> 422.
        assert (
            await client.post(
                f"/api/admin/sections/{SECTION_ID}/video",
                headers=env["headers"],
                json={"videoUrl": "https://vimeo.com/123456"},
            )
        ).status_code == 422

        # Add mode attaches normalized URL inside a transaction.
        add_r = await client.post(
            f"/api/admin/sections/{SECTION_ID}/video",
            headers=env["headers"],
            json={"videoUrl": "https://youtu.be/V_F6byM7Xyo?t=45", "mode": "add", "maxQuestions": 2},
        )
        assert add_r.status_code == 200
        assert add_r.json()["updated"] == 1
        assert "V_F6byM7Xyo" in add_r.json()["videoUrl"]
        assert add_r.json()["videoTitle"] == "1. Highlights"

        # Remove single URL mode.
        rem_r = await client.post(
            f"/api/admin/sections/{SECTION_ID}/video",
            headers=env["headers"],
            json={"mode": "remove", "removeUrl": add_r.json()["videoUrl"]},
        )
        assert rem_r.status_code == 200 and rem_r.json()["videoUrl"] is None
        assert any("where section_id = $1 and video_url = $2" in sql.lower() for _, sql, _ in env["db"].calls)


# --------------------------------------------------------------------------- 7. Questions CRUD, PATCH & Bulk


async def test_question_create_and_patch_validation_invariants(env):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", _sample_quiz())
    env["db"].on("fetchval", "select document_id from doc_sections where id = $1 and quiz_id = $2", lambda _s, a: DOC_ID if a[0] == SECTION_ID else None)
    env["db"].on("fetchval", "select 1 from doc_images where id = $1 and quiz_id = $2", lambda _s, a: 1 if a[0] in (IMAGE_ID, REVEAL_IMG_ID) else None)
    env["db"].on("fetchrow", "insert into questions", {"id": QUESTION_ID})

    async with _client() as client:
        # Whitespace-only question text -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions",
                headers=env["headers"],
                json={"kind": "mcq", "text": "          ", "options": ["A", "B", "C", "D"], "correctIndex": 0},
            )
        ).status_code == 422

        # Duplicate options -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions",
                headers=env["headers"],
                json={"kind": "mcq", "text": "Which region grew fastest?", "options": ["West", "west", "East", "South"], "correctIndex": 0},
            )
        ).status_code == 422

        # Over-110-char option -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions",
                headers=env["headers"],
                json={"kind": "mcq", "text": "Which region grew fastest?", "options": ["x" * 111, "B", "C", "D"], "correctIndex": 0},
            )
        ).status_code == 422

        # Cross-quiz section/image rejected -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions",
                headers=env["headers"],
                json={"kind": "mcq", "text": "Which region grew fastest?", "options": ["A", "B", "C", "D"], "correctIndex": 0, "sectionId": MISSING_UUID},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions",
                headers=env["headers"],
                json={"kind": "mcq", "text": "Which region grew fastest?", "options": ["A", "B", "C", "D"], "correctIndex": 0, "imageId": "not-a-uuid"},
            )
        ).status_code == 422

        # Valid MCQ with BOTH imageId and revealImageId clears revealImageId cleanly.
        ok_create = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/questions",
            headers=env["headers"],
            json={
                "kind": "mcq",
                "text": "What does the Q3 chart show?",
                "options": ["Growth", "Flat", "Dip", "Loss"],
                "correctIndex": 0,
                "sectionId": SECTION_ID,
                "imageId": IMAGE_ID,
                "revealImageId": REVEAL_IMG_ID,
            },
        )
        assert ok_create.status_code == 200
        insert_args = [args for op, sql, args in env["db"].calls if op == "fetchrow" and "insert into questions" in sql.lower()][-1]
        assert insert_args[3] == IMAGE_ID and insert_args[4] is None

        # PATCH validation tests on an existing manual picture MCQ.
        q_row = _sample_question(image_id=IMAGE_ID, source_type="image", flag_count=2, quality={"pulled": "flagged"}, status="pending")
        env["db"].on("fetchrow", "select * from questions where id = $1", lambda _s, a: dict(q_row) if a[0] == QUESTION_ID else None)

        # Invalid status, difficulty, or unknown field rejected (never silently ignored).
        assert (await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"status": "bogus"})).status_code == 422
        assert (await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"difficulty": "impossible"})).status_code == 422
        assert (await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"unknownField": 1})).status_code == 422

        # Picture -> reveal visual transition in one PATCH + unfiling manual question clears document_id + approving clears flags in tx.
        tx_before = env["db"].tx_count
        p = await client.patch(
            f"/api/admin/questions/{QUESTION_ID}",
            headers=env["headers"],
            json={"imageId": None, "revealImageId": REVEAL_IMG_ID, "sectionId": None, "status": "approved", "text": "Updated question wording?"},
        )
        assert p.status_code == 200
        assert env["db"].tx_count == tx_before + 1
        upd_sql, upd_args = [(sql, args) for op, sql, args in env["db"].calls if op == "execute" and "update questions set" in sql.lower()][-1]
        assert "reveal_image_id" in upd_sql and REVEAL_IMG_ID in upd_args
        assert "document_id" in upd_sql and "embedding" in upd_sql
        assert any("delete from question_flags where question_id = $1" in sql.lower() for _, sql, _ in env["db"].calls)

        # Attaching picture to a fact_fake question via PATCH -> 422.
        ff_row = _sample_question(kind="fact_fake", options=None, correct_index=None, is_true=True)
        env["db"].on("fetchrow", "select * from questions where id = $1", lambda _s, a: dict(ff_row) if a[0] == QUESTION_ID else None)
        assert (await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"imageId": IMAGE_ID})).status_code == 422


async def test_bulk_questions_all_actions_and_validation(env):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", lambda _s, a: _sample_quiz() if a[0] == QUIZ_ID else None)
    env["db"].on("fetchval", "select document_id from doc_sections where id = $1 and quiz_id = $2", lambda _s, a: DOC_ID if a[0] == SECTION_ID else None)
    env["db"].on("execute", "update questions", "UPDATE 2")
    env["db"].on("execute", "delete from questions", "DELETE 2")

    async with _client() as client:
        # Missing quiz -> 404.
        assert (
            await client.post(
                f"/api/admin/quizzes/{MISSING_UUID}/questions/bulk",
                headers=env["headers"],
                json={"ids": [QUESTION_ID], "action": "approve"},
            )
        ).status_code == 404

        # Blank string in ids -> 422 (no None injected into uuid[]).
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions/bulk",
                headers=env["headers"],
                json={"ids": [""], "action": "delete"},
            )
        ).status_code == 422

        # set_difficulty without difficulty -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions/bulk",
                headers=env["headers"],
                json={"ids": [QUESTION_ID], "action": "set_difficulty"},
            )
        ).status_code == 422

        # set_section with foreign section -> 422.
        assert (
            await client.post(
                f"/api/admin/quizzes/{QUIZ_ID}/questions/bulk",
                headers=env["headers"],
                json={"ids": [QUESTION_ID], "action": "set_section", "sectionId": MISSING_UUID},
            )
        ).status_code == 422

        # All 7 actions succeed cleanly.
        for payload in (
            {"ids": [QUESTION_ID], "action": "approve"},
            {"ids": [QUESTION_ID], "action": "reject"},
            {"ids": [QUESTION_ID], "action": "pin"},
            {"ids": [QUESTION_ID], "action": "unpin"},
            {"ids": [QUESTION_ID], "action": "set_difficulty", "difficulty": "hard"},
            {"ids": [QUESTION_ID], "action": "set_section", "sectionId": SECTION_ID},
            {"ids": [QUESTION_ID], "action": "set_section", "sectionId": None},
            {"ids": [QUESTION_ID], "action": "delete"},
        ):
            r = await client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/bulk", headers=env["headers"], json=payload)
            assert r.status_code == 200 and r.json()["changed"] == 2

        # Bulk approve cleared question_flags inside a transaction.
        assert any("delete from question_flags where question_id = any" in sql.lower() for _, sql, _ in env["db"].calls)
        # Unfiling via bulk set_section=None resets document_id for manual questions.
        assert any("case when origin = 'manual' then null else document_id end" in sql.lower() for _, sql, _ in env["db"].calls)


async def test_question_suggest_and_generate_more_routes(env, monkeypatch):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", _sample_quiz())

    async with _client() as client:
        # Suggest: LLM failure -> 502; LLM success -> trimmed 3 wrong answers.
        async def fail_parse(**kwargs):
            raise RuntimeError("rate limit")

        monkeypatch.setattr(llm, "parse", fail_parse)
        r_fail = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/questions/suggest",
            headers=env["headers"],
            json={"text": "What was Q3 revenue?", "correctAnswer": "$184M"},
        )
        assert r_fail.status_code == 502

        async def ok_parse(**kwargs):
            return admin_router.Suggestion(
                wrong_answers=[" $120M ", "$150M", "$210M", "$999M"],
                title=" Top Line ",
                explanation=" Revenue reached $184M. ",
                concern="",
            )

        monkeypatch.setattr(llm, "parse", ok_parse)
        r_ok = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/questions/suggest",
            headers=env["headers"],
            json={"text": "What was Q3 revenue?", "correctAnswer": "$184M"},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["wrongAnswers"] == ["$120M", "$150M", "$210M"]

        # Generate more: no ready docs -> 409 "Upload a report first."
        g1 = await client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/generate", headers=env["headers"])
        assert g1.status_code == 409 and "upload a report" in g1.json()["detail"].lower()

        # Ready doc currently writing -> 409 "already being written"
        env["db"].on("fetchval", "select 1 from documents where quiz_id = $1 and status = 'ready'", 1)
        g2 = await client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/generate", headers=env["headers"])
        assert g2.status_code == 409 and "already being written" in g2.json()["detail"].lower()

        # Idle ready doc claimed -> starts background top-up.
        env["db"].on("fetch", "update documents set stage = 'writing'", [{"id": DOC_ID}])
        started: list[str] = []
        monkeypatch.setattr(pipeline, "start_more_questions", lambda did: started.append(did))
        g3 = await client.post(f"/api/admin/quizzes/{QUIZ_ID}/questions/generate", headers=env["headers"])
        assert g3.status_code == 200 and started == [DOC_ID]


# --------------------------------------------------------------------------- 8. Results, CSV, Reset & Live


async def test_results_csv_and_reset_results(env, monkeypatch):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", _sample_quiz())

    async def fake_csv(quiz, rules):
        return "Name,Email\n'=CMD,evil@example.com\n"

    monkeypatch.setattr(stats, "participants_csv", fake_csv)

    async with _client() as client:
        csv_resp = await client.get(f"/api/admin/quizzes/{QUIZ_ID}/results.csv", headers=env["headers"])
        assert csv_resp.status_code == 200
        assert csv_resp.headers["content-type"].startswith("text/csv")
        assert 'filename="q3-review-results.csv"' in csv_resp.headers["content-disposition"]

        tx_before = env["db"].tx_count
        reset = await client.post(f"/api/admin/quizzes/{QUIZ_ID}/reset-results", headers=env["headers"])
        assert reset.status_code == 200
        assert env["db"].tx_count == tx_before + 1
        sqls = [sql.lower() for _, sql, _ in env["db"].calls]
        assert any("delete from runs where quiz_id = $1" in s for s in sqls)
        assert any("delete from live_sessions where quiz_id = $1" in s for s in sqls)
        assert any("not ilike '%picture%'" in s for s in sqls)
        assert QUIZ_ID in env["invalidated"]


async def test_live_admin_routes_and_tiebreak_order(env, monkeypatch):
    env["db"].on("fetchrow", "select * from quizzes where id = $1", lambda _s, a: _sample_quiz() if a[0] == QUIZ_ID else None)

    async with _client() as client:
        # Missing quiz -> 404.
        assert (await client.get(f"/api/admin/quizzes/{MISSING_UUID}/live", headers=env["headers"])).status_code == 404

        # Start live surfaces LiveError status cleanly (e.g. 409 when < 3 questions).
        async def fail_create(*args, **kwargs):
            raise live.LiveError("Need at least three questions.", 409)

        monkeypatch.setattr(live, "create_session", fail_create)
        assert (await client.post(f"/api/admin/quizzes/{QUIZ_ID}/live", headers=env["headers"], json={})).status_code == 409

        # Reclaim surfaces LiveError (409 finished).
        async def fail_reclaim(_sid):
            raise live.LiveError("Already finished.", 409)

        monkeypatch.setattr(live, "reclaim_session", fail_reclaim)
        assert (await client.post(f"/api/admin/live/{SESSION_ID}/reclaim", headers=env["headers"])).status_code == 409

        # Live results returns 404 for unknown session and orders ties by correct_count desc, joined_at asc.
        assert (await client.get(f"/api/admin/live/{MISSING_UUID}/results", headers=env["headers"])).status_code == 404

        env["db"].on("fetchval", "select 1 from live_sessions where id = $1", lambda _s, a: 1 if a[0] == SESSION_ID else None)
        env["db"].on(
            "fetch",
            "from live_participants lp",
            [{"name": "Alice", "email": "a@example.com", "team": "Ops", "score": 1200, "correct_count": 5, "head_start": 0}],
        )
        res = await client.get(f"/api/admin/live/{SESSION_ID}/results", headers=env["headers"])
        assert res.status_code == 200 and len(res.json()["standings"]) == 1
        sql_used = [sql.lower() for op, sql, _ in env["db"].calls if op == "fetch" and "from live_participants" in sql.lower()][-1]
        assert "lp.score desc, lp.correct_count desc, lp.joined_at asc" in sql_used

        # Finish live session succeeds and audits.
        async def ok_finish(_sid):
            return {"ok": True, "quizId": QUIZ_ID, "code": "ABCD"}

        monkeypatch.setattr(live, "finish_session", ok_finish)
        fin = await client.post(f"/api/admin/live/{SESSION_ID}/finish", headers=env["headers"])
        assert fin.status_code == 200 and fin.json()["ok"] is True
        assert any("insert into admin_audit_log" in sql.lower() for _, sql, _ in env["db"].calls)


async def test_admin_audit_enhancements_june_style_clear_flags_lost_picture_passwords_and_duplicate(env, monkeypatch):
    june_style = settings_store.DEFAULTS["host"]["hosts"][2]["style"]
    assert len(june_style) > 300

    # 1. _clean_host preserves June's 451-char style instead of truncating at 300.
    cleaned_host = settings_store._clean_host({"hosts": [{"name": "June", "voice": "eleven:southern", "manner": "wiseguy", "style": june_style}]})
    assert cleaned_host["hosts"][0]["style"] == june_style

    # 2. _clean_branding normalizes empty/whitespace support_email to None.
    cleaned_branding = settings_store._clean_branding({"support_email": "   "})
    assert cleaned_branding["support_email"] is None

    async with _client() as client:
        # 3. POST /host/sample accepts June's 451-char style without 422.
        async def fake_sample_audio(sarcasm, name, voice, **kwargs):
            return ("Well bless your heart!", b"RIFFfake")

        monkeypatch.setattr(admin_router.host_service, "sample_audio", fake_sample_audio)
        samp = await client.post(
            "/api/admin/host/sample",
            headers=env["headers"],
            json={"sarcasm": 6, "name": "June", "voice": "eleven:southern", "style": june_style, "manner": "wiseguy"},
        )
        assert samp.status_code == 200 and samp.json()["line"] == "Well bless your heart!"

        # 4. Change own password & reset another admin's password.
        pw_hash = security.hash_password("old-password-123")
        env["db"].on("fetchrow", "select id, password_hash from admin_users where id = $1", {"id": ADMIN_ID, "password_hash": pw_hash})
        bad_pw = await client.post(
            "/api/admin/me/password",
            headers=env["headers"],
            json={"currentPassword": "wrong-password", "newPassword": "new-secure-password-99"},
        )
        assert bad_pw.status_code == 400
        ok_pw = await client.post(
            "/api/admin/me/password",
            headers=env["headers"],
            json={"currentPassword": "old-password-123", "newPassword": "new-secure-password-99"},
        )
        assert ok_pw.status_code == 200

        env["db"].on(
            "fetchrow",
            "update admin_users set password_hash = $2 where id = $1 returning id, email",
            lambda _s, a: {"id": OTHER_ADMIN_ID, "email": "other@example.com"} if a[0] == OTHER_ADMIN_ID else None,
        )
        assert (
            await client.post(
                f"/api/admin/admins/{MISSING_UUID}/reset-password",
                headers=env["headers"],
                json={"password": "temp-password-1234"},
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/admin/admins/{OTHER_ADMIN_ID}/reset-password",
                headers=env["headers"],
                json={"password": "temp-password-1234"},
            )
        ).status_code == 200

        # 5. Clear flags on a 1-flag live question without changing status.
        live_flagged = {
            "id": QUESTION_ID,
            "quiz_id": QUIZ_ID,
            "kind": "mcq",
            "source_type": "text",
            "status": "approved",
            "flag_count": 1,
            "quality": {},
            "image_id": None,
            "question_text": "What was revenue?",
            "options": ["A", "B", "C", "D"],
            "correct_index": 0,
            "is_true": None,
            "origin": "generated",
        }
        env["db"].on("fetchrow", "select * from questions where id = $1", live_flagged)
        cf = await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"clearFlags": True})
        assert cf.status_code == 200
        assert any("delete from question_flags where question_id = $1" in sql.lower() for _, sql, _ in env["db"].calls)

        # 6. Approving a lost-picture question (source_type='image', image_id=None) -> 422.
        lost_pic = {**live_flagged, "source_type": "image", "image_id": None, "status": "pending"}
        env["db"].on("fetchrow", "select * from questions where id = $1", lost_pic)
        lp = await client.patch(f"/api/admin/questions/{QUESTION_ID}", headers=env["headers"], json={"status": "approved"})
        assert lp.status_code == 422

        # 7. Bulk pending action accepted cleanly.
        env["db"].on("fetchrow", "select * from quizzes where id = $1", _sample_quiz())
        bp = await client.post(
            f"/api/admin/quizzes/{QUIZ_ID}/questions/bulk",
            headers=env["headers"],
            json={"ids": [QUESTION_ID], "action": "pending"},
        )
        assert bp.status_code == 200

