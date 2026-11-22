"""Admin API: branding, host, quizzes, documents, the question bank, results and live games."""
from __future__ import annotations

import base64
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field, ValidationError

import db
import llm
from config import config
from dependencies import current_admin
from ingest import MIME_TYPES, extension_of, supported_extensions
from ingest import pipeline
from routers.play import throttle, throttle_key
from security import hash_password, issue_admin_token, verify_password
from services import bank, host_service, live, music_service, settings_store, stats, video
from services import eleven, host_samples
from services.settings_store import ALL_DECADES, DEFAULTS, MODES, VOICES
from storage import storage

log = logging.getLogger("admin")
router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024
_UNSAFE_SVG_RE = re.compile(
    rb"<(?:script|foreignObject|iframe|embed|object|animate\w*|set|use)\b"
    rb"|\bon[a-z]+\s*="
    rb"|javascript\s*:|vbscript\s*:|data\s*:|&#|@import|expression\s*\("
    rb"|(?:xlink:)?href\s*=\s*['\"]?\s*(?:https?:|//|%|javascript:|data:)",
    flags=re.I,
)


# --------------------------------------------------------------------------- sign-in

class Login(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
async def login(body: Login, request: Request) -> dict:
    throttle(request, "admin-login", 12)
    throttle_key(f"admin-login:{body.email.lower()}", 10, window=900)   # ten tries per account per quarter hour
    admin = await db.fetchrow("select * from admin_users where lower(email) = lower($1)", body.email)
    if admin is None or not verify_password(admin["password_hash"], body.password):
        raise HTTPException(401, "That email and password do not match an admin account.")
    await db.execute("update admin_users set last_login_at = now() where id = $1", admin["id"])
    return {"token": issue_admin_token(admin["id"]),
            "admin": {"id": admin["id"], "email": admin["email"], "name": admin["name"]}}


@router.get("/me")
async def me(admin: dict = Depends(current_admin)) -> dict:
    return {"admin": admin}


async def _voice_info() -> dict:
    """The voices, and for the ElevenLabs ones whether the account has them yet (they must be added once)."""
    info = dict(settings_store.VOICE_INFO)
    present = await eleven.voices_in_account()
    for voice_id, has in (present or {}).items():
        info[voice_id] = {**info[voice_id], "inAccount": has}
    return info


@router.get("/meta")
async def meta(admin: dict = Depends(current_admin)) -> dict:
    defaults = await settings_store.get("game")
    return {"modes": [settings_store.mode_view(key, defaults) for key in MODES], "decades": ALL_DECADES, "voices": VOICES,
            "voiceInfo": await _voice_info(), "elevenConfigured": eleven.configured(),
            "manners": [{"id": key, "label": host_service.MANNERS[key]["label"], "blurb": host_service.MANNERS[key]["blurb"]}
                        for key in settings_store.HOST_MANNERS],
            "extensions": supported_extensions(), "aiConfigured": config.ai_configured,
            "gameDefaults": defaults, "webBaseUrl": config.web_base_url or config.public_base_url}


class NewAdmin(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=80)
    password: str = Field(min_length=10, max_length=200)


# A game's host lines come to roughly this many characters (measured; see the README): for "about N games left".
CHARACTERS_PER_GAME = 2300


@router.get("/voice-usage")
async def voice_usage(admin: dict = Depends(current_admin)) -> dict:
    """How much of the ElevenLabs plan has been used this month: the host's voice is paid for by the character."""
    if not eleven.configured():
        return {"configured": False}
    try:
        info = await eleven.account()
    except eleven.ElevenError as exc:
        return {"configured": True, "error": str(exc)}
    except Exception:  # noqa: BLE001 – a network problem; the page says so rather than failing
        return {"configured": True, "error": "ElevenLabs could not be reached just now."}
    used, limit = int(info.get("character_count") or 0), int(info.get("character_limit") or 0)
    reset = info.get("next_character_count_reset_unix")
    return {"configured": True, "tier": info.get("tier"), "used": used, "limit": limit,
            "resetsAt": datetime.fromtimestamp(reset, tz=timezone.utc).isoformat() if reset else None,
            "perGame": CHARACTERS_PER_GAME,
            "gamesLeft": max(0, (limit - used) // CHARACTERS_PER_GAME) if limit else None}


@router.get("/admins")
async def list_admins(admin: dict = Depends(current_admin)) -> dict:
    return {"admins": await db.fetch("select id, email, name, created_at, last_login_at from admin_users order by created_at")}


@router.post("/admins")
async def add_admin(body: NewAdmin, admin: dict = Depends(current_admin)) -> dict:
    if await db.fetchval("select 1 from admin_users where lower(email) = lower($1)", body.email):
        raise HTTPException(409, "There is already an admin with that email.")
    row = await db.fetchrow("insert into admin_users (email, name, password_hash) values ($1,$2,$3)"
                            " returning id, email, name", body.email.lower(), body.name, hash_password(body.password))
    return {"admin": row}


@router.delete("/admins/{admin_id}")
async def remove_admin(admin_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    aid = str(admin_id)
    if aid == admin["id"]:
        raise HTTPException(400, "You cannot remove your own account.")
    await db.execute("delete from admin_users where id = $1", aid)
    return {"ok": True}


# --------------------------------------------------------------------------- company settings

@router.get("/settings/{key}")
async def read_settings(key: Literal["branding", "host", "game"], admin: dict = Depends(current_admin)) -> dict:
    return {"value": await settings_store.get(key), "defaults": DEFAULTS[key]}


@router.put("/settings/{key}")
async def write_settings(key: Literal["branding", "host", "game"], body: dict[str, Any],
                         admin: dict = Depends(current_admin)) -> dict:
    body.pop("logo_key", None)          # the logo is changed only through the upload endpoint
    value = await settings_store.put(key, body)
    if key in ("host", "branding"):
        host_samples.warm_soon()        # the hosts' introductions, recorded again if anything about them changed
    return {"value": value}


@router.post("/settings/branding/logo")
async def upload_logo(file: UploadFile = File(...), admin: dict = Depends(current_admin)) -> dict:
    extension = extension_of(file.filename or "")
    if extension not in (".png", ".jpg", ".jpeg", ".svg"):
        raise HTTPException(415, "Upload the logo as PNG, JPG or SVG.")
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(413, "That logo is larger than 2 MB.")
    if extension == ".svg" and _UNSAFE_SVG_RE.search(data):
        raise HTTPException(415, "That SVG contains active or external content. Export a plain static SVG or use PNG.")
    key = f"branding/logo-{secrets.token_hex(6)}{extension}"
    await storage().put(key, data, file.content_type or "application/octet-stream")
    current = await settings_store.get("branding")
    await db.execute(
        "insert into settings (key, value) values ('branding', $1) on conflict (key) do update set value = excluded.value,"
        " updated_at = now()", {**current, "logo_key": key})
    settings_store._cache.pop("branding", None)
    if current.get("logo_key"):
        await storage().delete_prefix(current["logo_key"])
    return {"value": await settings_store.get("branding")}


@router.delete("/settings/branding/logo")
async def remove_logo(admin: dict = Depends(current_admin)) -> dict:
    current = await settings_store.get("branding")
    if current.get("logo_key"):
        await storage().delete_prefix(current["logo_key"])
        await db.execute("update settings set value = $1, updated_at = now() where key = 'branding'",
                         {**current, "logo_key": None})
        settings_store._cache.pop("branding", None)
    return {"value": await settings_store.get("branding")}


class HostSample(BaseModel):
    sarcasm: int = Field(ge=0, le=10)
    name: str = Field(default="Max", max_length=30)
    voice: str | None = None        # give a voice to get the sample spoken as well as written
    quality: Literal["standard", "premium"] = "standard"
    pace: Literal["relaxed", "snappy", "rapid"] = "snappy"
    style: str | None = Field(default=None, max_length=300)   # an extra line about this character
    manner: Literal["wiseguy", "deadpan", "showman"] = "wiseguy"   # how this character performs


@router.post("/host/sample")
async def host_sample(body: HostSample, request: Request, admin: dict = Depends(current_admin)) -> dict:
    """What the host would say at this sarcasm level - and, with a voice, how it sounds."""
    throttle(request, "host-sample", 30)
    style = host_service.sarcasm_style(body.sarcasm)
    try:
        if body.voice:
            if body.voice not in VOICES:
                raise HTTPException(422, "Unknown voice.")
            line, wav = await host_service.sample_audio(body.sarcasm, body.name, body.voice,
                                                        quality=body.quality, pace=body.pace, style=body.style,
                                                        manner=body.manner)
            return {"line": line, "style": style,
                    "audio": "data:audio/wav;base64," + base64.b64encode(wav).decode()}
        return {"line": await host_service.sample_line(body.sarcasm, body.name), "style": style, "audio": None}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("host sample failed for voice=%s", body.voice)
        raise HTTPException(502, "The AI service could not produce a sample right now. Please try again.") from exc


class MusicRefresh(BaseModel):
    decade: str


@router.get("/music")
async def music_status(admin: dict = Depends(current_admin)) -> dict:
    rows = await db.fetch("select decade, count(*) as tracks from music_tracks where active group by decade")
    counts = {r["decade"]: r["tracks"] for r in rows}
    return {"decades": [{"decade": d, "tracks": counts.get(d, 0)} for d in ALL_DECADES]}


@router.post("/music/refresh")
async def music_refresh(body: MusicRefresh, admin: dict = Depends(current_admin)) -> dict:
    if body.decade not in ALL_DECADES:
        raise HTTPException(422, "Unknown decade.")
    music_service.ensure_decade(body.decade)
    return {"ok": True, "message": "Filling in the background; this takes a couple of minutes."}


# --------------------------------------------------------------------------- quizzes

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "quiz"


async def _quiz(quiz_id: str) -> dict:
    quiz = await db.fetchrow("select * from quizzes where id = $1", quiz_id)
    if quiz is None:
        raise HTTPException(404, "Quiz not found.")
    return quiz


def _quiz_view(quiz: dict) -> dict:
    base = config.web_base_url or config.public_base_url
    return {"id": quiz["id"], "slug": quiz["slug"], "title": quiz["title"], "description": quiz["description"],
            "meetingAt": quiz["meeting_at"], "status": quiz["status"], "accessCode": quiz["access_code"],
            "allowedEmailDomains": quiz["allowed_email_domains"], "teams": quiz["teams"],
            "settings": quiz["settings"], "createdAt": quiz["created_at"], "playUrl": f"{base}/q/{quiz['slug']}"}


class QuizCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    meetingAt: datetime | None = None


class QuizUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    meetingAt: datetime | None = None
    clearMeetingAt: bool = False
    status: Literal["draft", "open", "closed"] | None = None
    accessCode: str | None = Field(default=None, max_length=40)
    allowedEmailDomains: list[str] | None = None
    teams: list[str] | None = None
    settings: dict[str, Any] | None = None


@router.get("/quizzes")
async def list_quizzes(admin: dict = Depends(current_admin)) -> dict:
    quizzes = await db.fetch("select * from quizzes order by created_at desc")
    overviews = await stats.quiz_overviews([q["id"] for q in quizzes])
    return {"quizzes": [{**_quiz_view(q), "overview": overviews[q["id"]]} for q in quizzes]}


@router.post("/quizzes")
async def create_quiz(body: QuizCreate, admin: dict = Depends(current_admin)) -> dict:
    defaults = await settings_store.get("game")
    base = _slugify(body.title)
    slug = f"{base}-{secrets.token_hex(4)}" if defaults.get("unlisted") else base
    while await db.fetchval("select 1 from quizzes where slug = $1", slug):
        slug = f"{base}-{secrets.token_hex(4 if defaults.get('unlisted') else 2)}"
    quiz = await db.fetchrow(
        "insert into quizzes (slug, title, description, meeting_at, created_by) values ($1,$2,$3,$4,$5) returning *",
        slug, body.title.strip(), body.description, body.meetingAt, admin["id"])
    return {"quiz": _quiz_view(quiz)}


@router.get("/quizzes/{quiz_id}")
async def quiz_detail(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    quiz = await _quiz(qid)
    return {"quiz": _quiz_view(quiz), "rules": await settings_store.quiz_rules(quiz),
            "overview": await stats.quiz_overview(qid), "documents": await _documents(qid),
            "sections": await db.fetch(
                "select s.id, s.heading, s.ordinal, s.document_id,"
                " (select count(*) from questions q where q.section_id = s.id and q.status = 'approved') as questions,"
                " (select count(*) from questions q where q.section_id = s.id and q.status <> 'rejected') as total_questions,"
                " (select count(*) from questions q where q.section_id = s.id and q.video_url is not null) as videos,"
                " (select q.video_url from questions q where q.section_id = s.id and q.video_url is not null"
                "  order by q.created_at asc limit 1) as video_url,"
                " (select q.video_title from questions q where q.section_id = s.id and q.video_url is not null"
                "  order by q.created_at asc limit 1) as video_title,"
                " coalesce(("
                "   select jsonb_agg(jsonb_build_object('url', v.video_url, 'title', v.video_title, 'count', v.cnt)"
                "     order by v.first_at asc)"
                "   from ("
                "     select q.video_url, max(q.video_title) as video_title, count(*) as cnt, min(q.created_at) as first_at"
                "     from questions q where q.section_id = s.id and q.video_url is not null group by q.video_url"
                "   ) v"
                " ), '[]'::jsonb) as video_clips"
                " from doc_sections s"
                " join documents d on d.id = s.document_id where s.quiz_id = $1 order by d.created_at, s.ordinal", qid)}


@router.patch("/quizzes/{quiz_id}")
async def update_quiz(quiz_id: uuid.UUID, body: QuizUpdate, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    quiz = await _quiz(qid)
    changes: dict[str, Any] = {}
    if body.title is not None:
        changes["title"] = body.title.strip()
    if "description" in body.model_fields_set:
        changes["description"] = body.description
    if body.meetingAt is not None or body.clearMeetingAt:
        changes["meeting_at"] = None if body.clearMeetingAt else body.meetingAt
    revoke_memberships = False
    if "accessCode" in body.model_fields_set:
        new_code = (body.accessCode or "").strip() or None
        changes["access_code"] = new_code
        if new_code != (quiz["access_code"] or None):
            changes["access_version"] = int(quiz.get("access_version") or 1) + 1
            revoke_memberships = True
    if body.allowedEmailDomains is not None:
        changes["allowed_email_domains"] = [d.strip().lower().lstrip("@") for d in body.allowedEmailDomains if d.strip()]
    if body.teams is not None:
        changes["teams"] = list(dict.fromkeys(t.strip()[:60] for t in body.teams if t.strip()))
    if body.settings is not None:
        prior_rules = await settings_store.quiz_rules(quiz)
        merged = settings_store.deep_merge(quiz["settings"] or {}, body.settings)
        # Keep download vs purge mutually exclusive: purging deletes the raw file so download cannot work.
        if body.settings.get("purge_source_after_process"):
            merged["allow_download"] = False
        elif body.settings.get("allow_download"):
            merged["purge_source_after_process"] = False
        # Validate and clamp per-quiz overrides through clean_game without flattening inherited defaults.
        cleaned_full = settings_store.clean_game(settings_store.deep_merge(await settings_store.get("game"), merged))
        cleaned_overrides: dict[str, Any] = {}
        for k, v in merged.items():
            if k not in cleaned_full:
                continue
            if isinstance(v, dict) and isinstance(cleaned_full[k], dict):
                sub = {}
                for sk, sv in v.items():
                    if sk in cleaned_full[k]:
                        if isinstance(sv, dict) and isinstance(cleaned_full[k][sk], dict):
                            sub[sk] = {ssk: cleaned_full[k][sk][ssk] for ssk in sv if ssk in cleaned_full[k][sk]}
                        else:
                            sub[sk] = cleaned_full[k][sk]
                cleaned_overrides[k] = sub
            else:
                cleaned_overrides[k] = cleaned_full[k]
        changes["settings"] = cleaned_overrides
        # When an unpublished draft becomes unlisted, give it an unguessable slug suffix.
        if (
            cleaned_full.get("unlisted")
            and not prior_rules.get("unlisted")
            and quiz["status"] == "draft"
            and not re.search(r"-[0-9a-f]{8}$", quiz["slug"])
        ):
            base_slug = quiz["slug"][:40]
            candidate = f"{base_slug}-{secrets.token_hex(4)}"
            while await db.fetchval("select 1 from quizzes where slug = $1", candidate):
                candidate = f"{base_slug}-{secrets.token_hex(4)}"
            changes["slug"] = candidate
    if body.status is not None:
        if body.status == "open":
            effective_rules = await settings_store.quiz_rules({**quiz, "settings": changes.get("settings", quiz["settings"])})
            sources = effective_rules.get("question_sources", "all")
            origin_filter = "and origin = 'manual'" if sources == "manual" else ("and origin = 'generated'" if sources == "ai" else "")
            live_questions = await db.fetchval(
                f"select count(*) from questions where quiz_id = $1 and status = 'approved' and kind = 'mcq' {origin_filter}", qid)
            if live_questions < 3:
                raise HTTPException(409, "Add a report (or at least three approved multiple-choice questions matching "
                                         "your question-source filter) before opening the quiz.")
        changes["status"] = body.status
    if changes:
        columns = ", ".join(f"{name} = ${i}" for i, name in enumerate(changes, start=2))
        quiz = await db.fetchrow(f"update quizzes set {columns}, updated_at = now() where id = $1 returning *",
                                 qid, *changes.values())
        if revoke_memberships:
            await db.execute("delete from quiz_memberships where quiz_id = $1", qid)
        bank.invalidate(qid)
    return {"quiz": _quiz_view(quiz), "rules": await settings_store.quiz_rules(quiz)}


@router.delete("/quizzes/{quiz_id}")
async def delete_quiz(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    await _quiz(qid)
    for document in await db.fetch("select id from documents where quiz_id = $1", qid):
        pipeline.cancel_document(document["id"])
        await storage().delete_prefix(f"quizzes/images/{document['id']}")
        await storage().delete_prefix(f"quizzes/reports/{document['id']}")
    await db.execute("delete from quizzes where id = $1", qid)
    bank.invalidate(qid)
    return {"ok": True}


# --------------------------------------------------------------------------- documents

async def _documents(quiz_id: str) -> list[dict]:
    rows = await db.fetch(
        "select id, filename, role, status, stage, progress, error, warnings, title, summary, word_count, section_count,"
        " image_count, chunk_count, question_count, size_bytes, storage_key, created_at, processed_at from documents"
        " where quiz_id = $1 order by created_at", quiz_id)
    result = []
    for row in rows:
        item = dict(row)
        item["sourcePurged"] = not bool(item.pop("storage_key", ""))
        result.append(item)
    return result


@router.get("/quizzes/{quiz_id}/documents")
async def list_documents(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    return {"documents": await _documents(str(quiz_id))}


@router.post("/quizzes/{quiz_id}/documents")
async def upload_document(quiz_id: uuid.UUID, file: UploadFile = File(...), admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    await _quiz(qid)
    filename = re.sub(r"[^\w.\- ()]+", "_", (file.filename or "document").split("/")[-1].split("\\")[-1])[:160]
    extension = extension_of(filename)
    if extension not in supported_extensions():
        hint = " Open it in Word and save as .docx." if extension == ".doc" else ""
        raise HTTPException(415, f"That file type is not supported. Use {', '.join(supported_extensions())}.{hint}")
    if not config.ai_configured:
        raise HTTPException(503, "No AI provider is configured, so questions cannot be written yet.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 60 MB.")
    if not data:
        raise HTTPException(422, "That file is empty.")
    first = not await db.fetchval("select 1 from documents where quiz_id = $1", qid)
    document = await db.fetchrow(
        "insert into documents (quiz_id, filename, storage_key, mime_type, size_bytes, role) values ($1,$2,'',$3,$4,$5)"
        " returning id", qid, filename, MIME_TYPES.get(extension), len(data), "primary" if first else "supporting")
    key = f"quizzes/reports/{document['id']}/{filename}"
    await storage().put(key, data, MIME_TYPES.get(extension, "application/octet-stream"))
    await db.execute("update documents set storage_key = $2 where id = $1", document["id"], key)
    pipeline.start_processing(document["id"])
    return {"documents": await _documents(qid)}


@router.post("/documents/{document_id}/reprocess")
async def reprocess_document(document_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    did = str(document_id)
    claimed = await db.fetchrow(
        "update documents set status = 'processing', stage = 'reading', heartbeat_at = now(), error = null"
        " where id = $1 and storage_key <> ''"
        " and ((status <> 'processing' and stage is null) or coalesce(heartbeat_at, created_at) < now() - interval '10 minutes')"
        " returning quiz_id",
        did,
    )
    if claimed is None:
        document = await db.fetchrow("select status, storage_key from documents where id = $1", did)
        if document is None:
            raise HTTPException(404, "Document not found.")
        if not document["storage_key"]:
            raise HTTPException(409, "The source file was purged after processing. Upload the report again to reprocess it.")
        raise HTTPException(409, "This document is still being processed.")
    pipeline.start_processing(did)
    bank.invalidate(claimed["quiz_id"])
    return {"ok": True}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    did = str(document_id)
    document = await db.fetchrow("select quiz_id from documents where id = $1", did)
    if document is None:
        raise HTTPException(404, "Document not found.")
    pipeline.cancel_document(did)
    # Any hand-written or cross-document picture question pointing at this document's images moves to review.
    await db.execute(
        "update questions set status = 'pending', image_id = null,"
        " quality = coalesce(quality, '{}'::jsonb)"
        " || jsonb_build_object('pulled', 'Its picture was removed when a report was deleted. Choose a picture again.')"
        " where image_id in (select id from doc_images where document_id = $1)",
        did,
    )
    await db.execute(
        "update questions set reveal_image_id = null where reveal_image_id in (select id from doc_images where document_id = $1)",
        did,
    )
    await storage().delete_prefix(f"quizzes/images/{did}")
    await storage().delete_prefix(f"quizzes/reports/{did}")
    await db.execute("delete from documents where id = $1", did)
    bank.invalidate(document["quiz_id"])
    return {"ok": True}


class VideoInspect(BaseModel):
    url: str = Field(min_length=5, max_length=300)


class SectionVideoWrite(BaseModel):
    videoUrl: str | None = Field(default=None, max_length=300)
    videoTitle: str | None = Field(default=None, max_length=120)
    externalOnly: bool | None = None
    maxQuestions: int = Field(default=2, ge=1, le=20)
    mode: Literal["add", "replace", "remove"] = "add"
    removeUrl: str | None = Field(default=None, max_length=300)


@router.post("/video/inspect")
async def inspect_video(body: VideoInspect, request: Request, admin: dict = Depends(current_admin)) -> dict:
    throttle(request, "video-inspect", 30)
    try:
        return await video.inspect_youtube(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/sections/{section_id}/video")
async def set_section_video(section_id: uuid.UUID, body: SectionVideoWrite, admin: dict = Depends(current_admin)) -> dict:
    sid = str(section_id)
    section = await db.fetchrow("select id, quiz_id, heading from doc_sections where id = $1", sid)
    if section is None:
        raise HTTPException(404, "Section not found.")
    if body.mode == "remove" or (not body.videoUrl or not body.videoUrl.strip()):
        async with db.transaction() as tx:
            if body.removeUrl:
                await tx.execute(
                    "update questions set video_url = null, video_title = null, edited_at = now(), edited_by = $3"
                    " where section_id = $1 and video_url = $2",
                    sid, body.removeUrl, admin["id"],
                )
            else:
                await tx.execute(
                    "update questions set video_url = null, video_title = null, edited_at = now(), edited_by = $2"
                    " where section_id = $1 and video_url is not null",
                    sid, admin["id"],
                )
        bank.invalidate(section["quiz_id"])
        return {"updated": 0, "videoUrl": None, "videoTitle": None}
    try:
        stored_url = video.normalize_url(body.videoUrl, external_only=body.externalOnly)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    title = (body.videoTitle or "").strip()[:120] or section["heading"][:120]
    async with db.transaction() as tx:
        if body.mode == "replace":
            await tx.execute("update questions set video_url = null, video_title = null where section_id = $1", sid)
            targets = await tx.fetch(
                "select id from questions where section_id = $1 and status <> 'rejected' and kind = 'mcq'"
                " order by pinned desc, created_at asc limit $2",
                sid, body.maxQuestions,
            )
            if not targets:
                targets = await tx.fetch(
                    "select id from questions where section_id = $1 and status <> 'rejected'"
                    " order by pinned desc, created_at asc limit $2",
                    sid, body.maxQuestions,
                )
        else:
            # Additive mode: never wipe unrelated clips already paired with other questions in this section.
            targets = await tx.fetch(
                "select id from questions where section_id = $1 and status <> 'rejected'"
                " and (video_url is null or video_url = $2)"
                " order by (video_url = $2) desc, (kind = 'mcq') desc, pinned desc, created_at asc limit $3",
                sid, stored_url, body.maxQuestions,
            )
            if not targets:
                targets = await tx.fetch(
                    "select id from questions where section_id = $1 and status <> 'rejected'"
                    " order by (kind = 'mcq') desc, pinned desc, created_at asc limit $2",
                    sid, body.maxQuestions,
                )
        for row in targets:
            await tx.execute(
                "update questions set video_url = $2, video_title = $3, edited_at = now(), edited_by = $4, updated_at = now()"
                " where id = $1",
                row["id"], stored_url, title, admin["id"],
            )
    bank.invalidate(section["quiz_id"])
    return {"updated": len(targets), "videoUrl": stored_url, "videoTitle": title}


# --------------------------------------------------------------------------- question bank

_QUESTION_COLUMNS = ("q.id, q.kind, q.source_type, q.skill, q.title, q.question_text, q.options, q.correct_index, q.is_true,"
                     " q.explanation, q.source_quote, q.video_url, q.video_title, q.difficulty, q.status, q.origin, q.pinned,"
                     " q.edited_at, q.flag_count, q.times_served, q.times_correct, q.quality, q.section_id, q.image_id,"
                     " q.reveal_image_id, q.created_at")


def _question_view(row: dict) -> dict:
    served = row["times_served"]
    return {"id": row["id"], "kind": row["kind"], "sourceType": row["source_type"], "skill": row["skill"],
            "title": row["title"], "text": row["question_text"], "options": row["options"],
            "correctIndex": row["correct_index"], "isTrue": row["is_true"], "explanation": row["explanation"],
            "sourceQuote": row["source_quote"], "videoUrl": row.get("video_url"), "videoTitle": row.get("video_title"),
            "difficulty": row["difficulty"], "status": row["status"],
            "origin": row["origin"], "edited": row["edited_at"] is not None, "pinned": row["pinned"],
            "flags": row["flag_count"], "served": served,
            "accuracy": round(100 * row["times_correct"] / served) if served else None,
            "pulledReason": (row["quality"] or {}).get("pulled"), "sectionId": row["section_id"],
            "section": row.get("heading"), "imageId": row["image_id"],
            "imageUrl": live._image_url(row["image_id"]) if row["image_id"] else None,
            "revealImageId": row.get("reveal_image_id"),
            "revealImageUrl": live._image_url(row["reveal_image_id"]) if row.get("reveal_image_id") else None}


@router.get("/quizzes/{quiz_id}/questions")
async def list_questions(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    await _quiz(qid)
    rows = await db.fetch(
        f"select {_QUESTION_COLUMNS}, s.heading from questions q left join doc_sections s on s.id = q.section_id"
        " left join documents d on d.id = s.document_id where q.quiz_id = $1"
        " order by q.pinned desc, d.created_at nulls last, s.ordinal nulls last, q.kind desc, q.created_at", qid)
    flags = await db.fetch(
        "select f.question_id, f.reason, f.created_at from question_flags f join questions q on q.id = f.question_id"
        " where q.quiz_id = $1 order by f.created_at desc", qid)
    reasons: dict[str, list[str]] = {}
    for flag in flags:
        if flag["reason"]:
            reasons.setdefault(flag["question_id"], []).append(flag["reason"])
    questions = [{**_question_view(r), "flagReasons": reasons.get(r["id"], [])} for r in rows]
    images = await db.fetch(
        "select id, caption, description from doc_images"
        " where quiz_id = $1 and (usable is not false"
        "   or id in (select image_id from questions where quiz_id = $1 and image_id is not null"
        "             union select reveal_image_id from questions where quiz_id = $1 and reveal_image_id is not null))"
        " order by ordinal",
        qid,
    )
    return {"questions": questions,
            "images": [{"id": i["id"], "label": i["caption"] or i["description"] or "Picture",
                        "url": live._image_url(i["id"])} for i in images]}


class QuestionWrite(BaseModel):
    kind: Literal["mcq", "fact_fake"] = "mcq"
    title: str | None = Field(default=None, max_length=60)
    text: str = Field(min_length=8, max_length=300)
    options: list[str] | None = None
    correctIndex: int | None = None
    isTrue: bool | None = None
    explanation: str | None = Field(default=None, max_length=500)
    videoUrl: str | None = Field(default=None, max_length=300)
    videoTitle: str | None = Field(default=None, max_length=120)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    sectionId: str | None = None
    imageId: str | None = None
    revealImageId: str | None = None
    pinned: bool = False
    status: Literal["pending", "approved", "rejected"] = "approved"


def _uuid_or_none(value: Any, what: str) -> str | None:
    """An id from the browser, or a clear refusal (never a database error) if it is not one."""
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(422, f"That {what} id is not valid.") from exc


def _validated(body: QuestionWrite) -> dict:
    if body.kind == "mcq":
        options = [o.strip() for o in (body.options or []) if o and o.strip()]
        if len(options) != 4 or len({o.lower() for o in options}) != 4:
            raise HTTPException(422, "A multiple-choice question needs four different answers.")
        if body.correctIndex is None or not (0 <= body.correctIndex < 4):
            raise HTTPException(422, "Mark which answer is correct.")
        return {"options": options, "correct_index": body.correctIndex, "is_true": None}
    if body.isTrue is None:
        raise HTTPException(422, "Say whether the statement is a fact or a fake.")
    return {"options": None, "correct_index": None, "is_true": body.isTrue}


@router.post("/quizzes/{quiz_id}/questions")
async def create_question(quiz_id: uuid.UUID, body: QuestionWrite, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    await _quiz(qid)
    shape = _validated(body)
    section_id = _uuid_or_none(body.sectionId, "section")
    image_id = _uuid_or_none(body.imageId, "picture")
    reveal_image_id = _uuid_or_none(body.revealImageId, "reveal visual")
    document_id = None
    if section_id:
        document_id = await db.fetchval("select document_id from doc_sections where id = $1 and quiz_id = $2",
                                        section_id, qid)
        if document_id is None:
            raise HTTPException(422, "That section does not belong to this quiz.")
    if image_id and not await db.fetchval("select 1 from doc_images where id = $1 and quiz_id = $2", image_id, qid):
        raise HTTPException(422, "That picture does not belong to this quiz.")
    if reveal_image_id and not await db.fetchval("select 1 from doc_images where id = $1 and quiz_id = $2", reveal_image_id, qid):
        raise HTTPException(422, "That reveal visual does not belong to this quiz.")
    if image_id:
        reveal_image_id = None
    try:
        video_url = video.normalize_url(body.videoUrl)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    video_title = (body.videoTitle or "").strip()[:120] or None
    row = await db.fetchrow(
        "insert into questions (quiz_id, document_id, section_id, image_id, reveal_image_id, kind, source_type, skill,"
        " title, question_text, options, correct_index, is_true, explanation, video_url, video_title, difficulty, status,"
        " origin, pinned)"
        " values ($1,$2,$3,$4,$5,$6,$7,'recall',$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'manual',$18) returning id",
        qid, document_id, section_id, image_id, reveal_image_id, body.kind, "image" if image_id else "text",
        (body.title or "").strip() or None, body.text.strip(), shape["options"], shape["correct_index"],
        shape["is_true"], (body.explanation or "").strip() or None, video_url, video_title,
        body.difficulty, body.status, body.pinned)
    bank.invalidate(qid)
    return {"id": row["id"]}


@router.patch("/questions/{question_id}")
async def update_question(question_id: uuid.UUID, body: dict[str, Any], admin: dict = Depends(current_admin)) -> dict:
    qid_str = str(question_id)
    question = await db.fetchrow("select * from questions where id = $1", qid_str)
    if question is None:
        raise HTTPException(404, "Question not found.")
    changes: dict[str, Any] = {}
    content_changed = False
    curated = False
    approving = body.get("status") == "approved"
    if "status" in body and body["status"] in ("pending", "approved", "rejected"):
        changes["status"] = body["status"]
        if body["status"] == "rejected":
            curated = True
        if approving:
            changes["flag_count"] = 0
            changes["quality"] = {k: v for k, v in (question["quality"] or {}).items() if k != "pulled"}
    if "pinned" in body:
        changes["pinned"] = bool(body["pinned"])
        if changes["pinned"]:
            curated = True
    if "difficulty" in body and body["difficulty"] in ("easy", "medium", "hard"):
        changes["difficulty"] = body["difficulty"]
        curated = True
    if "sectionId" in body:
        section_id = _uuid_or_none(body["sectionId"], "section")
        if section_id:
            document_id = await db.fetchval("select document_id from doc_sections where id = $1 and quiz_id = $2",
                                            section_id, question["quiz_id"])
            if document_id is None:
                raise HTTPException(422, "That section does not belong to this quiz.")
            changes["document_id"] = document_id
        changes["section_id"] = section_id
        curated = True
    if "imageId" in body:
        image_id = _uuid_or_none(body["imageId"], "picture")
        if image_id and not await db.fetchval("select 1 from doc_images where id = $1 and quiz_id = $2",
                                              image_id, question["quiz_id"]):
            raise HTTPException(422, "That picture does not belong to this quiz.")
        changes["image_id"] = image_id
        changes["source_type"] = "image" if image_id else ("text" if question["source_type"] == "image" else question["source_type"])
        if image_id:
            changes["reveal_image_id"] = None
            # Restoring a picture clears any stale "lost picture" pull reason.
            if (question["quality"] or {}).get("pulled"):
                changes["quality"] = {k: v for k, v in (question["quality"] or {}).items() if k != "pulled"}
        curated = True
    final_image_id = changes["image_id"] if "image_id" in changes else question["image_id"]
    if "revealImageId" in body and not final_image_id:
        reveal_image_id = _uuid_or_none(body["revealImageId"], "reveal visual")
        if reveal_image_id and not await db.fetchval("select 1 from doc_images where id = $1 and quiz_id = $2",
                                                     reveal_image_id, question["quiz_id"]):
            raise HTTPException(422, "That reveal visual does not belong to this quiz.")
        changes["reveal_image_id"] = reveal_image_id
        curated = True
    if "title" in body:
        changes["title"] = (str(body["title"] or "")).strip()[:60] or None
        curated = True
    if "explanation" in body:
        changes["explanation"] = (str(body["explanation"] or "")).strip()[:500] or None
        curated = True
    if "videoUrl" in body:
        try:
            changes["video_url"] = video.normalize_url(str(body["videoUrl"] or ""))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        curated = True
    if "videoTitle" in body:
        changes["video_title"] = (str(body["videoTitle"] or "")).strip()[:120] or None
        curated = True
    if any(key in body for key in ("text", "options", "correctIndex", "isTrue")):
        try:
            merged = QuestionWrite(
                kind=question["kind"], text=body.get("text", question["question_text"]),
                options=body.get("options", question["options"]),
                correctIndex=body.get("correctIndex", question["correct_index"]),
                isTrue=body.get("isTrue", question["is_true"]))
        except ValidationError as exc:
            problem = exc.errors()[0]
            field = str(problem["loc"][0]) if problem.get("loc") else "question"
            raise HTTPException(422, {"text": "The question must be between 8 and 300 characters."}.get(
                field, f"Check the {field}: {problem['msg']}.")) from exc
        shape = _validated(merged)
        changes.update({"question_text": merged.text.strip(), **shape})
        content_changed = True
        curated = True
    if not changes:
        return {"ok": True}
    if curated:
        changes["edited_at"] = datetime.now().astimezone()
        changes["edited_by"] = admin["id"]
    if content_changed:
        changes["embedding"] = None
    columns = ", ".join(f"{name} = ${i}" for i, name in enumerate(changes, start=2))
    await db.execute(f"update questions set {columns}, updated_at = now() where id = $1", qid_str, *changes.values())
    if approving:
        await db.execute("delete from question_flags where question_id = $1", qid_str)   # dealt with: start clean
    bank.invalidate(question["quiz_id"])
    return {"ok": True}


@router.delete("/questions/{question_id}")
async def delete_question(question_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    row = await db.fetchrow("delete from questions where id = $1 returning quiz_id", str(question_id))
    if row:
        bank.invalidate(row["quiz_id"])
    return {"ok": True}


class BulkAction(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["approve", "reject", "delete", "pin", "unpin", "set_difficulty", "set_section"]
    difficulty: Literal["easy", "medium", "hard"] | None = None
    sectionId: str | None = None


@router.post("/quizzes/{quiz_id}/questions/bulk")
async def bulk_questions(quiz_id: uuid.UUID, body: BulkAction, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    ids = [_uuid_or_none(value, "question") for value in body.ids]
    if body.action == "reject":
        result = await db.execute(
            "update questions set status = 'rejected', edited_at = now(), edited_by = $3, updated_at = now()"
            " where quiz_id = $1 and id = any($2::uuid[])", qid, ids, admin["id"])
    elif body.action == "pin":
        result = await db.execute(
            "update questions set pinned = true, edited_at = now(), edited_by = $3, updated_at = now()"
            " where quiz_id = $1 and id = any($2::uuid[])", qid, ids, admin["id"])
    elif body.action == "set_difficulty":
        if not body.difficulty:
            raise HTTPException(422, "Choose a difficulty level.")
        result = await db.execute(
            "update questions set difficulty = $3, edited_at = now(), edited_by = $4, updated_at = now()"
            " where quiz_id = $1 and id = any($2::uuid[])", qid, ids, body.difficulty, admin["id"])
    elif body.action == "set_section":
        sec_id = _uuid_or_none(body.sectionId, "section")
        doc_id = None
        if sec_id:
            doc_id = await db.fetchval("select document_id from doc_sections where id = $1 and quiz_id = $2", sec_id, qid)
            if doc_id is None:
                raise HTTPException(422, "That section does not belong to this quiz.")
        result = await db.execute(
            "update questions set section_id = $3, document_id = coalesce($4, document_id),"
            " edited_at = now(), edited_by = $5, updated_at = now()"
            " where quiz_id = $1 and id = any($2::uuid[])", qid, ids, sec_id, doc_id, admin["id"])
    else:
        statement = {
            "approve": "update questions set status = 'approved', flag_count = 0, updated_at = now(),"
                       " quality = coalesce(quality, '{}'::jsonb) - 'pulled'",
            "unpin": "update questions set pinned = false, updated_at = now()",
            "delete": "delete from questions",
        }[body.action]
        result = await db.execute(f"{statement} where quiz_id = $1 and id = any($2::uuid[])", qid, ids)
    if body.action == "approve":
        await db.execute("delete from question_flags where question_id = any($1::uuid[])"
                         " and question_id in (select id from questions where quiz_id = $2)", ids, qid)
    bank.invalidate(qid)
    return {"changed": int(result.split()[-1])}


class SuggestRequest(BaseModel):
    text: str = Field(min_length=8, max_length=300)
    correctAnswer: str = Field(min_length=1, max_length=110)
    sectionId: str | None = None


class Suggestion(BaseModel):
    wrong_answers: list[str] = Field(description="Exactly three believable but wrong answers")
    title: str = Field(description="A short punny title, 2-5 words, that does not give the answer away")
    explanation: str = Field(description="One sentence stating the correct fact")
    concern: str = Field(description="Empty if the question is sound; otherwise one sentence on what is wrong with it")


@router.post("/quizzes/{quiz_id}/questions/suggest")
async def suggest(quiz_id: uuid.UUID, body: SuggestRequest, request: Request, admin: dict = Depends(current_admin)) -> dict:
    """Help with a hand-written question: propose wrong answers and sanity-check it against the report."""
    throttle(request, "suggest", 40)
    qid = str(quiz_id)
    await _quiz(qid)
    section_id = _uuid_or_none(body.sectionId, "section")
    passages = await db.fetch(
        "select chunk_text from doc_chunks where quiz_id = $1 and ($2::uuid is null or section_id = $2)"
        " order by ordinal limit 14", qid, section_id)
    context = "\n\n".join(p["chunk_text"] for p in passages)[:9000] or "(no report text available)"
    try:
        result = await llm.parse(
            model=config.model_writer, schema=Suggestion, effort="low", max_output_tokens=1200,
            system=("An organiser is writing a multiple-choice quiz question about a report by hand. Propose three wrong "
                    "answers that are believable but clearly wrong according to the report: the same kind of thing, same "
                    "format and unit, similar length as the correct answer. No joke answers. Also give a short punny "
                    "title and a one-sentence explanation. If the report contradicts their correct answer, or the "
                    "question is ambiguous, say so in 'concern'; otherwise leave 'concern' empty."),
            user=f"REPORT EXCERPTS\n{context}\n\nQUESTION: {body.text}\nCORRECT ANSWER: {body.correctAnswer}")
    except Exception as exc:  # noqa: BLE001
        log.exception("question suggest failed for quiz=%s", qid)
        raise HTTPException(502, "The AI service could not suggest answers right now. Please try again.") from exc
    return {"wrongAnswers": [w.strip() for w in result.wrong_answers][:3], "title": result.title.strip(),
            "explanation": result.explanation.strip(), "concern": result.concern.strip()}


@router.post("/quizzes/{quiz_id}/questions/generate")
async def generate_more(quiz_id: uuid.UUID, request: Request, admin: dict = Depends(current_admin)) -> dict:
    throttle(request, "generate-more", 10)
    qid = str(quiz_id)
    await _quiz(qid)
    claimed = await db.fetch(
        "update documents set stage = 'writing', heartbeat_at = now()"
        " where quiz_id = $1 and status = 'ready'"
        " and (stage is null or coalesce(heartbeat_at, created_at) < now() - interval '10 minutes')"
        " returning id",
        qid,
    )
    if not claimed:
        has_ready = await db.fetchval("select 1 from documents where quiz_id = $1 and status = 'ready'", qid)
        if has_ready:
            raise HTTPException(409, "Questions are already being written for this report.")
        raise HTTPException(409, "Upload a report first.")
    for document in claimed:
        pipeline.start_more_questions(document["id"])
    return {"ok": True, "message": "Writing more questions in the background."}


# --------------------------------------------------------------------------- results

@router.get("/quizzes/{quiz_id}/results")
async def results(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    quiz = await _quiz(qid)
    rules = await settings_store.quiz_rules(quiz)
    people = await stats.participants(quiz, rules)
    boards = {mode: await stats.leaderboard(qid, mode, limit=10) for mode in rules["modes"]}
    feedback = await db.fetchrow("select round(avg(stars), 1) as average, count(*) as count from feedback where quiz_id = $1", qid)
    comments = await db.fetch("select stars, comment, created_at from feedback where quiz_id = $1 and comment is not null"
                              " and comment <> '' order by created_at desc limit 20", qid)
    return {"participants": people, "ready": sum(1 for p in people if p["ready"]), "threshold": rules["ready_threshold"],
            "leaderboards": boards, "insights": await stats.insights(qid),
            "feedback": {"average": float(feedback["average"]) if feedback["average"] is not None else None,
                         "count": feedback["count"], "comments": comments}}


@router.get("/quizzes/{quiz_id}/results.csv")
async def results_csv(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> Response:
    qid = str(quiz_id)
    quiz = await _quiz(qid)
    body = await stats.participants_csv(quiz, await settings_store.quiz_rules(quiz))
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{quiz["slug"]}-results.csv"'})


@router.post("/quizzes/{quiz_id}/reset-results")
async def reset_results(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    """Clear solo runs, live sessions, leaderboards, flags and feedback for a quiz (e.g. after admin play-testing),
    leaving the uploaded report, sections and questions untouched."""
    qid = str(quiz_id)
    await _quiz(qid)
    async with db.transaction() as tx:
        await tx.execute("delete from feedback where quiz_id = $1", qid)
        await tx.execute("delete from runs where quiz_id = $1", qid)
        await tx.execute("delete from live_sessions where quiz_id = $1", qid)
        await tx.execute("delete from question_flags where question_id in (select id from questions where quiz_id = $1)", qid)
        # Restore questions that were auto-pulled by play-test flags/stats, while leaving lost-picture pulls in review.
        await tx.execute(
            "update questions set status = 'approved'"
            " where quiz_id = $1 and status = 'pending' and (quality ? 'pulled')"
            " and coalesce(quality->>'pulled', '') not ilike '%picture%'",
            qid,
        )
        await tx.execute(
            "update questions set times_served = 0, times_correct = 0, flag_count = 0,"
            " quality = case when coalesce(quality->>'pulled', '') ilike '%picture%' then quality"
            "   else coalesce(quality, '{}'::jsonb) - 'pulled' end"
            " where quiz_id = $1",
            qid,
        )
    bank.invalidate(qid)
    return {"ok": True}


# --------------------------------------------------------------------------- live games

class LiveCreate(BaseModel):
    decade: str | None = None


@router.post("/quizzes/{quiz_id}/live")
async def start_live(quiz_id: uuid.UUID, body: LiveCreate, admin: dict = Depends(current_admin)) -> dict:
    qid = str(quiz_id)
    quiz = await _quiz(qid)
    try:
        return await live.create_session(quiz, await settings_store.quiz_rules(quiz), body.decade)
    except live.LiveError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/quizzes/{quiz_id}/live")
async def list_live(quiz_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    sessions = await db.fetch(
        "select s.id, s.code, s.status, s.created_at, s.finished_at, jsonb_array_length(s.items) as questions,"
        " (select count(*) from live_participants p where p.session_id = s.id) as players from live_sessions s"
        " where s.quiz_id = $1 order by s.created_at desc limit 20", str(quiz_id))
    return {"sessions": sessions}


@router.post("/live/{session_id}/reclaim")
async def reclaim_live(session_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    try:
        return await live.reclaim_session(str(session_id))
    except live.LiveError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/live/{session_id}/results")
async def live_results(session_id: uuid.UUID, admin: dict = Depends(current_admin)) -> dict:
    standings = await db.fetch(
        "select lp.display_name as name, p.email, lp.team, lp.score, lp.correct_count, lp.head_start"
        " from live_participants lp join players p on p.id = lp.player_id where lp.session_id = $1"
        " order by lp.score desc", str(session_id))
    return {"standings": standings}
