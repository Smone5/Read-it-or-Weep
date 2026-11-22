"""Player-facing API: look up a quiz, join it, play a game."""
from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

import db
from config import config
from dependencies import current_player, optional_player
from security import TokenError, issue_player_token, read_admin_token, sign_media
from services import eleven, bank, engine, favorites, host_samples, host_service, music_service, settings_store, stats
from services.settings_store import MODES

_log = logging.getLogger("play")
router = APIRouter(prefix="/api", tags=["play"])

# A bounded in-memory brake on abuse. Generous, because a whole office can share one IP address.
_hits: dict[str, deque] = defaultdict(deque)
_MAX_THROTTLE_KEYS = 4000
_TRUSTED_NETS = [ipaddress.ip_network(cidr, strict=False) for cidr in config.trusted_proxies]


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_NETS)


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    # Trust X-Forwarded-For only when the immediate peer is a configured proxy/tunnel/private ALB.
    if _is_trusted_proxy(host):
        raw = request.headers.get("x-forwarded-for") or ""
        hops = [part.strip() for part in raw.split(",") if part.strip()]
        # Walk right-to-left to find the first untrusted client hop (prevents client-supplied header spoofing).
        for hop in reversed(hops):
            if not _is_trusted_proxy(hop):
                return hop
        if hops:
            return hops[0]
    return host


def throttle(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    throttle_key(f"{bucket}:{_client_ip(request)}", limit, window)


def _prune_throttle(key: str, window: int, now: float) -> deque:
    if len(_hits) > _MAX_THROTTLE_KEYS:
        stale = [k for k, dq in list(_hits.items()) if not dq or now - dq[-1] > 3600]
        for k in stale[: len(stale)]:
            _hits.pop(k, None)
        while len(_hits) > _MAX_THROTTLE_KEYS:
            _hits.pop(next(iter(_hits)), None)
    hits = _hits[key]
    while hits and now - hits[0] > window:
        hits.popleft()
    return hits


def throttle_key(key: str, limit: int, window: int = 60) -> None:
    """The same brake for anything that should be counted by something other than the caller's address."""
    now = time.monotonic()
    hits = _prune_throttle(key, window, now)
    if len(hits) >= limit:
        raise HTTPException(429, "Too many requests. Give it a moment and try again.")
    hits.append(now)


def check_failed_code_throttle(request: Request, bucket: str, limit: int = 20, window: int = 300) -> None:
    """Refuse further access-code tries from an IP that has already racked up too many *wrong* guesses."""
    key = f"{bucket}:{_client_ip(request)}"
    hits = _prune_throttle(key, window, time.monotonic())
    if len(hits) >= limit:
        raise HTTPException(429, "Too many wrong access codes. Wait a few minutes and try again.")


def record_failed_code(request: Request, bucket: str, window: int = 300) -> None:
    """Count only failed access-code attempts so valid corporate-NAT logins never self-DoS."""
    key = f"{bucket}:{_client_ip(request)}"
    now = time.monotonic()
    hits = _prune_throttle(key, window, now)
    hits.append(now)


async def _is_admin(token: str | None) -> bool:
    if not token:
        return False
    try:
        admin_id = read_admin_token(token)
    except TokenError:
        return False
    return bool(await db.fetchval("select 1 from admin_users where id = $1", admin_id))


def _quiz_is_restricted(quiz: dict) -> bool:
    return bool(quiz.get("access_code")) or bool(quiz.get("allowed_email_domains"))


def quiz_grant(quiz: dict) -> str:
    return f"{quiz['id']}:{quiz.get('access_version') or 1}"


def merged_grants(existing_player: dict | None, player_id: str, quiz: dict, *, grant_access: bool) -> list[str]:
    grants: list[str] = []
    if existing_player and existing_player.get("id") == player_id:
        grants.extend(sorted(existing_player.get("_quiz_grants") or ()))
    if grant_access and quiz.get("access_code"):
        grant = quiz_grant(quiz)
        if grant not in grants:
            grants.append(grant)
    return grants


def _email_allowed(quiz: dict, email: str) -> bool:
    domains = [d.lower().lstrip("@") for d in (quiz.get("allowed_email_domains") or [])]
    if not domains:
        return True
    return email.strip().lower().split("@")[-1] in domains


async def record_quiz_membership(quiz_id: str, player_id: str) -> None:
    await db.execute(
        "insert into quiz_memberships (quiz_id, player_id) values ($1, $2) on conflict do nothing",
        quiz_id, player_id,
    )


async def has_quiz_access(quiz: dict, player: dict | None, *, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    if player is None:
        return False
    if not _email_allowed(quiz, player["email"]):
        return False
    if quiz.get("access_code"):
        if quiz_grant(quiz) not in (player.get("_quiz_grants") or set()):
            return False
    if not _quiz_is_restricted(quiz):
        return True
    return bool(await db.fetchval(
        "select 1 from quiz_memberships where quiz_id = $1 and player_id = $2",
        quiz["id"], player["id"],
    ))


async def require_quiz_access(quiz: dict, player: dict | None, *, is_admin: bool = False) -> None:
    if not await has_quiz_access(quiz, player, is_admin=is_admin):
        raise HTTPException(403, "Join this quiz with an eligible email and access code first.")


async def find_quiz(slug: str, admin_token: str | None = None) -> dict:
    quiz = await db.fetchrow("select * from quizzes where slug = $1", slug.lower())
    if quiz is None:
        raise HTTPException(404, "We could not find that quiz. Check the link you were sent.")
    if quiz["status"] != "open" and not await _is_admin(admin_token):
        message = "This quiz has closed." if quiz["status"] == "closed" else "This quiz is not open yet."
        raise HTTPException(403, message)
    return quiz


def _host_choices(host: dict) -> list[dict]:
    """The hosts a player may pick from. An ElevenLabs voice carries its own label and CRT register;
    OpenAI voices are described relative to each other (deeper / brighter voice)."""
    hosts = host["hosts"]
    if not host["player_can_choose"] or len(hosts) < 2:
        return []
    pitch = {c["id"]: settings_store.VOICE_FACTS.get(c["voice"], (180, 6))[0] for c in hosts}
    low, high = min(pitch.values()), max(pitch.values())
    choices = []
    for c in hosts:
        if eleven.is_eleven(c["voice"]):
            voice = eleven.BY_ID[c["voice"]]
            choices.append({"id": c["id"], "name": c["name"], "label": voice.short, "register": voice.register})
        else:
            reg = "deeper" if pitch[c["id"]] == low and low != high else (
                "brighter" if pitch[c["id"]] == high and low != high else "middle")
            choices.append({"id": c["id"], "name": c["name"], "label": None, "register": reg})
    return choices


async def _branding_view() -> dict:
    branding = await settings_store.get("branding")
    host = await settings_store.get("host")
    return {"companyName": branding["company_name"], "gameTitle": branding["game_title"],
            "tagline": branding["tagline"], "primaryColor": branding["primary_color"],
            "accentColor": branding["accent_color"], "theme": branding["theme"],
            "logoUrl": f"/api/media/logo?v={abs(hash(branding['logo_key'])) % 100000}" if branding["logo_key"] else None,
            "supportEmail": branding["support_email"],
            "host": {"name": host["name"], "voiceEnabled": bool(host["voice_enabled"]),
                     "talkEnabled": bool(host["voice_enabled"] and host["talk_enabled"]),
                     "choices": _host_choices(host)}}


@router.get("/health")
async def health() -> dict:
    await db.fetchval("select 1")
    return {"ok": True}


@router.get("/config")
async def site_config(player: dict | None = Depends(optional_player)) -> dict:
    quizzes = await db.fetch(
        "select id, slug, title, description, meeting_at, teams, settings from quizzes"
        " where status = 'open'"
        " order by case when meeting_at is not null and meeting_at >= now() - interval '1 day' then 0 else 1 end,"
        " meeting_at asc nulls last, created_at desc limit 60")
    items = []
    for q in quizzes:
        rules = await settings_store.quiz_rules(q)
        if rules.get("unlisted"):
            continue
        entry: dict[str, Any] = {
            "slug": q["slug"], "title": q["title"], "description": q["description"],
            "meetingAt": q["meeting_at"], "teams": q["teams"] or [],
        }
        if player is not None:
            ready = await stats.readiness(q["id"], player["id"], int(rules["ready_threshold"]))
            entry["readiness"] = {
                "cleared": ready["cleared"], "total": ready["total"],
                "percent": ready["percent"], "ready": ready["ready"],
            }
        items.append(entry)
    return {"branding": await _branding_view(), "quizzes": items,
            "player": {"name": player["name"], "team": player["team"]} if player else None}


@router.get("/quiz/{slug}")
async def quiz_info(slug: str, request: Request, player: dict | None = Depends(optional_player),
                    x_admin_token: str | None = Header(default=None)) -> dict:
    throttle(request, "quiz-info", 120)
    admin = await _is_admin(x_admin_token)
    quiz = await find_quiz(slug, x_admin_token)
    rules = await settings_store.quiz_rules(quiz)
    authorized = await has_quiz_access(quiz, player, is_admin=admin)
    stock = bank.counts(await bank.approved(quiz["id"], rules["question_sources"]))
    sections = await stats.askable_sections(quiz["id"])
    music = bool(rules["music_enabled"])
    report_files: list[dict] = []
    if rules.get("allow_download") and authorized:
        docs = await db.fetch(
            "select id, filename from documents where quiz_id = $1 and status = 'ready' and storage_key <> ''"
            " order by created_at asc", quiz["id"])
        for d in docs:
            if admin and quiz["status"] != "open":
                exp, sig = sign_media(f"doc:{d['id']}:admin", ttl_seconds=3600)
                url = f"/api/media/document/{d['id']}?exp={exp}&sig={sig}&admin=1"
            else:
                exp, sig = sign_media(f"doc:{d['id']}", ttl_seconds=3600)
                url = f"/api/media/document/{d['id']}?exp={exp}&sig={sig}"
            report_files.append({"id": d["id"], "filename": d["filename"], "url": url})
    view = {
        "branding": await _branding_view(),
        "quiz": {
            "slug": quiz["slug"], "title": quiz["title"], "description": quiz["description"],
            "meetingAt": quiz["meeting_at"], "status": quiz["status"], "requiresCode": bool(quiz["access_code"]),
            "teams": quiz["teams"], "sections": len(sections), "questions": stock["mcq"] + stock["fact_fake"],
            "ready": stock["mcq"] >= 3, "leaderboard": bool(rules["leaderboard"]),
            "shareable": not (rules.get("unlisted") or bool(quiz.get("access_code"))),
            "reportUrl": rules.get("report_url") if authorized else None,
            "reportFiles": report_files,
            "modes": [{**settings_store.mode_view(m, rules), "music": music and m in ("show", "sudden_death", "jukebox")}
                      for m in rules["modes"]],
            "defaultMode": rules["default_mode"], "decades": rules["decades"] if music else [],
        },
        "player": None,
    }
    if player and authorized:
        view["player"] = await _player_view(player, quiz, rules)
    return view


async def _player_view(player: dict, quiz: dict, rules: dict) -> dict:
    unfinished = await db.fetchrow(
        "select * from runs where player_id = $1 and quiz_id = $2 and status = 'in_progress'"
        " and started_at > now() - interval '2 hours' order by started_at desc limit 1", player["id"], quiz["id"])
    if unfinished is not None and _speed_clock_expired(unfinished):
        await engine.finish_run(unfinished, rules, abandoned=False)
        unfinished = None
    best = await db.fetch("select mode, max(score) as score from runs where player_id = $1 and quiz_id = $2"
                          " and status = 'completed' group by mode", player["id"], quiz["id"])
    return {"id": player["id"], "name": player["name"], "email": player["email"], "team": player["team"],
            "favoriteArtist": player.get("favorite_artist"),
            "readiness": await stats.readiness(quiz["id"], player["id"], int(rules["ready_threshold"])),
            "best": {b["mode"]: b["score"] for b in best},
            "unfinishedRun": {"id": unfinished["id"], "mode": unfinished["mode"]} if unfinished else None}


class JoinRequest(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: EmailStr
    team: str | None = Field(default=None, max_length=60)
    accessCode: str | None = Field(default=None, max_length=40)


async def upsert_player(name: str, email: str, team: str | None) -> dict:
    name = re.sub(r"\s+", " ", name).strip()
    return await db.fetchrow(
        "insert into players (email, name, team, last_seen_at) values ($1, $2, $3, now())"
        " on conflict (lower(email)) do update set name = excluded.name,"
        " team = coalesce(excluded.team, players.team), last_seen_at = now() returning *",
        email.strip().lower(), name, team or None)


@router.get("/quiz/{slug}/hosts/{host_id}/sample")
async def host_sample(slug: str, host_id: str, request: Request, x_admin_token: str | None = Header(default=None),
                      if_none_match: str | None = Header(default=None)) -> Response:
    """A host character introducing themselves, so a player can choose a host by ear.

    Recorded the first time anyone asks, then kept; the other hosts are recorded in the background so the
    next tap is instant. The address stays the same when a host changes, so the browser checks back (ETag).
    """
    throttle(request, "host-sample", 120)
    quiz = await find_quiz(slug, x_admin_token)
    rules = await settings_store.quiz_rules(quiz)
    allow_eleven = not bool(rules.get("confidential_voice"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", host_id):
        raise HTTPException(404, "There is no such host.")
    try:
        found = await host_samples.sample(host_id, allow_eleven=allow_eleven)
    except Exception as exc:  # noqa: BLE001 – the voice services can be down; the lobby carries on without it
        _log.warning("host sample for %s failed: %s", host_id, exc)
        raise HTTPException(503, "That host cannot be heard right now. Try again in a minute.") from exc
    if found is None:
        raise HTTPException(404, "There is no such host.")
    if allow_eleven:
        host_samples.warm_soon()
    tag = f'"{found["key"]}"'
    headers = {"ETag": tag, "Cache-Control": "no-cache"}
    if if_none_match == tag:
        return Response(status_code=304, headers=headers)
    return Response(found["audio"], media_type=found["mime"], headers=headers)


class FavouriteBody(BaseModel):
    artist: str | None = Field(default=None, max_length=80)


@router.put("/me/favourite-artist")
async def set_favourite_artist(body: FavouriteBody, request: Request, player: dict = Depends(current_player)) -> dict:
    """The player's favourite artist: now and then a song from them, or from artists like them, plays behind their
    games. Empty clears it. The name is checked against the music catalogue and kept as the catalogue spells it."""
    throttle(request, "favourite", 30)
    name = " ".join((body.artist or "").split())
    if not name:
        await db.execute("update players set favorite_artist = null where id = $1", player["id"])
        return {"artist": None, "similar": []}
    found = await favorites.pool(name)
    if found is None:
        raise HTTPException(404, "We could not find that artist. Check the spelling, or try another.")
    await db.execute("update players set favorite_artist = $2 where id = $1", player["id"], found["artist"])
    return {"artist": found["artist"], "similar": found["similar"][:4]}


@router.post("/quiz/{slug}/join")
async def join(slug: str, body: JoinRequest, request: Request,
               existing: dict | None = Depends(optional_player),
               x_admin_token: str | None = Header(default=None)) -> dict:
    throttle(request, "join", 120)
    admin = await _is_admin(x_admin_token)
    quiz = await find_quiz(slug, x_admin_token)
    email = body.email.strip().lower()
    if not admin and not _email_allowed(quiz, email):
        raise HTTPException(403, "That email address is not eligible for this quiz. Use your work email.")
    if not admin and quiz["access_code"]:
        code_bucket = f"join-code:{quiz['id']}"
        check_failed_code_throttle(request, code_bucket)
        given = (body.accessCode or "").strip().lower()
        expected = quiz["access_code"].strip().lower()
        if not hmac.compare_digest(given, expected):
            record_failed_code(request, code_bucket)
            raise HTTPException(403, "That access code is not right. Check the invitation.")
    team = body.team if (body.team and (not quiz["teams"] or body.team in quiz["teams"])) else None
    if quiz["teams"] and not team:
        raise HTTPException(422, "Pick your team.")
    player = await upsert_player(body.name, email, team)
    await record_quiz_membership(quiz["id"], player["id"])
    grants = merged_grants(existing, player["id"], quiz, grant_access=True)
    rules = await settings_store.quiz_rules(quiz)
    return {"token": issue_player_token(player["id"], quiz_grants=grants),
            "player": await _player_view(player, quiz, rules)}


@router.get("/quiz/{slug}/leaderboard")
async def quiz_leaderboard(slug: str, mode: str | None = None, team: str | None = None,
                           limit: int = 100, offset: int = 0, q: str | None = None,
                           player: dict = Depends(current_player),      # names and scores are for people who joined
                           x_admin_token: str | None = Header(default=None)) -> dict:
    admin = await _is_admin(x_admin_token)
    quiz = await find_quiz(slug, x_admin_token)
    await require_quiz_access(quiz, player, is_admin=admin)
    rules = await settings_store.quiz_rules(quiz)
    if not rules["leaderboard"]:
        raise HTTPException(403, "The leaderboard is switched off for this quiz.")
    mode = mode if mode in rules["modes"] else rules["default_mode"]
    return await stats.leaderboard(quiz["id"], mode, team or None, limit=limit, offset=offset, query=q, player_id=player["id"])


# --------------------------------------------------------------------------- playing

class StartRun(BaseModel):
    mode: str | None = None
    decade: str | None = None


class AnswerBody(BaseModel):
    itemId: uuid.UUID
    choiceIndex: int | None = None


class WagerBody(BaseModel):
    itemId: uuid.UUID
    amount: int
    readAloud: bool = False      # the page shows the question while the host reads it, then asks for its answers


class LifelineBody(BaseModel):
    itemId: uuid.UUID
    kind: str


class ItemBody(BaseModel):
    itemId: uuid.UUID


def _game_error(exc: engine.GameError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _load_run(run_id: uuid.UUID | str, player: dict) -> tuple[dict, dict, dict]:
    run = await db.fetchrow("select * from runs where id = $1", str(run_id))
    if run is None or run["player_id"] != player["id"]:
        raise HTTPException(404, "We could not find that game.")
    quiz = await db.fetchrow("select * from quizzes where id = $1", run["quiz_id"])
    return run, quiz, await settings_store.quiz_rules(quiz)


async def _turn(run: dict, rules: dict, deal: bool = True) -> dict:
    """The thing to show next: an item, or the fact that the game is over.

    With ``deal=False`` nothing new is dealt: the answer is the item already waiting (or none), so
    starting or re-opening a game never starts a question's clock behind the player's back.
    """
    item = await (engine.next_item(run, rules) if deal else engine.current_item(run))
    run = await db.fetchrow("select * from runs where id = $1", run["id"])
    finished = run["status"] != "in_progress" or (deal and item is None)
    return {"state": engine.run_state(run, rules), "finished": finished,
            "item": await engine.public_item(item, run) if item and not finished else None}


@router.post("/quiz/{slug}/runs")
async def start_run(slug: str, body: StartRun, request: Request, player: dict = Depends(current_player),
                    x_admin_token: str | None = Header(default=None)) -> dict:
    throttle(request, "start", 120)
    admin = await _is_admin(x_admin_token)
    quiz = await find_quiz(slug, x_admin_token)
    await require_quiz_access(quiz, player, is_admin=admin)
    rules = await settings_store.quiz_rules(quiz)
    try:
        run = await engine.start_run(quiz, rules, player, body.mode or rules["default_mode"], body.decade)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return await _turn(run, rules, deal=False)     # the first question is dealt once the title card is up


@router.get("/runs/{run_id}")
async def resume_run(run_id: uuid.UUID, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    if run["status"] == "in_progress" and _speed_clock_expired(run):
        run = await engine.finish_run(run, rules, abandoned=False)
    return await _turn(run, rules, deal=False)


@router.get("/runs/{run_id}/soundtrack")
async def run_soundtrack(run_id: uuid.UUID, player: dict = Depends(current_player)) -> dict:
    """Songs to play quietly behind this game (none for a Straight Quiz, or when music is switched off)."""
    run, _, rules = await _load_run(run_id, player)
    if not (rules["music_enabled"] and rules.get("soundtrack", True)) or run["mode"] == "straight":
        return {"decade": None, "tracks": [], "cues": {}}
    decade = music_service.game_decade(run, rules)
    if not decade:
        return {"decade": None, "tracks": [], "cues": {}}
    music_service.ensure_decade(decade)
    music_service.ensure_moods(decade)
    asked = {r["music_track_id"] for r in await db.fetch(
        "select music_track_id from run_items where run_id = $1 and music_track_id is not null", str(run_id))}
    rows = [r for r in await music_service.soundtrack_rows(decade, run["id"]) if r["id"] not in asked]
    tracks = [music_service._track_view(r) for r in rows]
    favs = await favorites.for_player(player["id"])
    tracks = favorites.mix(tracks, favs, run["id"], decade)
    fav_views = [t for t in tracks if t.get("because")]
    sections = await bank.sections(run["quiz_id"])
    cues = music_service.build_cues(rows, sections, favorite_tracks=fav_views, seed=run["id"])
    return {"decade": decade, "tracks": tracks, "cues": cues, "volume": rules["soundtrack_volume"], "mix": rules["mix"]}


@router.post("/runs/{run_id}/next")
async def next_turn(run_id: uuid.UUID, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    return await _turn(run, rules)


@router.post("/runs/{run_id}/open")
async def open_item(run_id: uuid.UUID, body: ItemBody, player: dict = Depends(current_player)) -> dict:
    """The title card is done: reveal a sealed item and start its clock."""
    run, _, rules = await _load_run(run_id, player)
    try:
        item, run = await engine.open_item(run, rules, str(body.itemId))
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/answer")
async def answer(run_id: uuid.UUID, body: AnswerBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    item_id = str(body.itemId)
    try:
        reveal = await engine.submit_answer(run, rules, item_id, body.choiceIndex)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    item = await db.fetchrow("select question_id from run_items where id = $1", item_id)
    if item and item["question_id"]:
        await stats.note_question_health(item["question_id"])
    return reveal


@router.post("/runs/{run_id}/wager")
async def wager(run_id: uuid.UUID, body: WagerBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.place_wager(run, str(body.itemId), body.amount, read_aloud=body.readAloud)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/start-clock")
async def start_clock(run_id: uuid.UUID, body: ItemBody, player: dict = Depends(current_player)) -> dict:
    """The final question has been read out, or the player skipped the reading: show its answers, start its clock."""
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.start_clock(run, str(body.itemId))
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/lifeline")
async def lifeline(run_id: uuid.UUID, body: LifelineBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.use_lifeline(run, str(body.itemId), body.kind, hint_writer=host_service.hint_with_status)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    run = await db.fetchrow("select * from runs where id = $1", str(run_id))
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/media-error")
async def media_error(run_id: uuid.UUID, body: ItemBody, request: Request,
                      player: dict = Depends(current_player)) -> dict:
    """The browser could not play a question's song (a music clip, or the song behind a jargon title). Work out whose
    fault that is, and carry on either way."""
    throttle(request, "media-error", 20)
    rid, iid = str(run_id), str(body.itemId)
    run, _, rules = await _load_run(rid, player)
    item = await db.fetchrow("select * from run_items where id = $1 and run_id = $2 and answered_at is null",
                             iid, rid)
    if item and item["slot"] in engine.CLIP_SLOTS:
        payload = item["payload"]
        preview_url = payload.get("preview_url")
        if not payload.get("no_clip") and preview_url:
            attempt = int(payload.get("attempt", 1))
            clip_gone = await music_service.preview_is_dead(preview_url)
            if clip_gone and item["music_track_id"]:
                await music_service.retire_track(item["music_track_id"])
            async with db.transaction() as tx:
                locked_run = await tx.fetchrow("select * from runs where id = $1 for update", rid)
                locked_item = await tx.fetchrow(
                    "select * from run_items where id = $1 and run_id = $2 and answered_at is null for update",
                    iid, rid,
                )
                if locked_run and locked_item and not locked_item["payload"].get("no_clip"):
                    if clip_gone and attempt < 3:
                        slot = {"slot": locked_item["slot"], "round": locked_item["round_no"],
                                "name": locked_item["round_name"], "limit": locked_item["time_limit_sec"],
                                "mult": float(locked_item["points_multiplier"]), "attempt": attempt + 1}
                        if payload.get("asks"):
                            slot["asks"] = payload["asks"]
                        deleted = await tx.fetchval(
                            "delete from run_items where id = $1 and answered_at is null returning id", locked_item["id"])
                        if deleted:
                            await tx.execute(
                                "update runs set position = greatest(0, position - 1), plan = $2::jsonb || plan where id = $1",
                                rid, [slot],
                            )
                    elif locked_run["mode"] == "jukebox":
                        rest = [{**slot, "no_clip": True} if slot["slot"] in ("jargon", "song_or_jargon") else slot
                                for slot in locked_run["plan"] if slot["slot"] != "music"]
                        if locked_item["slot"] == "jargon":
                            await tx.execute(
                                "update run_items set payload = payload || '{\"no_clip\": true}'::jsonb"
                                " where id = $1 and answered_at is null",
                                locked_item["id"],
                            )
                            await tx.execute("update runs set plan = $2 where id = $1", rid, rest)
                        else:
                            deleted = await tx.fetchval(
                                "delete from run_items where id = $1 and answered_at is null returning id", locked_item["id"])
                            if deleted:
                                await tx.execute(
                                    "update runs set position = greatest(0, position - 1), plan = $2 where id = $1",
                                    rid, rest,
                                )
                    else:
                        without_music = [slot for slot in locked_run["plan"] if slot["slot"] != "music"]
                        deleted = await tx.fetchval(
                            "delete from run_items where id = $1 and answered_at is null returning id", locked_item["id"])
                        if deleted:
                            await tx.execute(
                                "update runs set position = greatest(0, position - 1), plan = $2, decade = null where id = $1",
                                rid, without_music,
                            )
            run = await db.fetchrow("select * from runs where id = $1", rid)
    return await _turn(run, rules)


def _speed_clock_expired(run: dict) -> bool:
    return (
        run["mode"] == "speed"
        and run["deadline_at"] is not None
        and datetime.now(timezone.utc) >= run["deadline_at"]
    )


@router.post("/runs/{run_id}/quit")
async def quit_run(run_id: uuid.UUID, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    # If a Speed Run clock has genuinely run out, closing the run completes it instead of abandoning it.
    run = await engine.finish_run(run, rules, abandoned=not _speed_clock_expired(run))
    return {"summary": await stats.run_summary(run, rules)}


@router.get("/runs/{run_id}/summary")
async def summary(run_id: uuid.UUID, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    if run["status"] == "in_progress":
        if _speed_clock_expired(run):
            run = await engine.finish_run(run, rules, abandoned=False)
        else:
            raise HTTPException(409, "This game is still going.")
    return {"summary": await stats.run_summary(run, rules)}


class FlagBody(BaseModel):
    itemId: uuid.UUID
    reason: str | None = Field(default=None, max_length=300)


@router.post("/questions/flag")
async def flag_question(body: FlagBody, request: Request, player: dict = Depends(current_player)) -> dict:
    throttle(request, "flag", 40)
    item = await db.fetchrow("select question_id from run_items where id = $1 and player_id = $2",
                             str(body.itemId), player["id"])
    if item is None or not item["question_id"]:
        raise HTTPException(404, "We could not find that question.")
    inserted = await db.fetchval(
        "insert into question_flags (question_id, player_id, reason) values ($1,$2,$3)"
        " on conflict (question_id, player_id) do nothing returning 1",
        item["question_id"], player["id"], body.reason,
    )
    if inserted:
        await db.execute("update questions set flag_count = flag_count + 1 where id = $1", item["question_id"])
        await stats.note_question_health(item["question_id"])
    return {"ok": True}


class FeedbackBody(BaseModel):
    runId: uuid.UUID
    stars: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/feedback")
async def feedback(body: FeedbackBody, player: dict = Depends(current_player)) -> dict:
    """Rate a game. A player has one rating per game: tapping a different star replaces it."""
    run = await db.fetchrow("select id, quiz_id from runs where id = $1 and player_id = $2",
                            str(body.runId), player["id"])
    if run is None:
        raise HTTPException(404, "We could not find that game.")
    await db.execute("insert into feedback (player_id, quiz_id, run_id, stars, comment) values ($1,$2,$3,$4,$5)"
                     " on conflict (player_id, run_id) where run_id is not null do update"
                     " set stars = excluded.stars, comment = coalesce(excluded.comment, feedback.comment), created_at = now()",
                     player["id"], run["quiz_id"], run["id"], body.stars, body.comment)
    return {"ok": True}
