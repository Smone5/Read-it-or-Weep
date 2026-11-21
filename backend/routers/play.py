"""Player-facing API: look up a quiz, join it, play a game."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

import db
from dependencies import current_player, optional_player
from security import TokenError, issue_player_token, read_admin_token
from services import eleven, bank, engine, favorites, host_samples, host_service, music_service, settings_store, stats
from services.settings_store import MODES

_log = logging.getLogger("play")
router = APIRouter(prefix="/api", tags=["play"])

# A small in-memory brake on abuse. Generous, because a whole office can share one IP address.
_hits: dict[str, deque] = defaultdict(deque)


def throttle(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    throttle_key(f"{bucket}:{request.client.host if request.client else 'unknown'}", limit, window)


def throttle_key(key: str, limit: int, window: int = 60) -> None:
    """The same brake for anything that should be counted by something other than the caller's address."""
    now = time.monotonic()
    hits = _hits[key]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        raise HTTPException(429, "Too many requests. Give it a moment and try again.")
    hits.append(now)


def _is_admin(token: str | None) -> bool:
    if not token:
        return False
    try:
        read_admin_token(token)
        return True
    except TokenError:
        return False


async def find_quiz(slug: str, admin_token: str | None = None) -> dict:
    quiz = await db.fetchrow("select * from quizzes where slug = $1", slug.lower())
    if quiz is None:
        raise HTTPException(404, "We could not find that quiz. Check the link you were sent.")
    if quiz["status"] != "open" and not _is_admin(admin_token):
        message = "This quiz has closed." if quiz["status"] == "closed" else "This quiz is not open yet."
        raise HTTPException(403, message)
    return quiz


def _host_choices(host: dict) -> list[dict]:
    """The hosts a player may pick from. An ElevenLabs voice is described in a few words ("British wit");
    the others relative to each other (deeper / brighter voice)."""
    hosts = host["hosts"]
    if not host["player_can_choose"] or len(hosts) < 2:
        return []
    pitch = {c["id"]: settings_store.VOICE_FACTS.get(c["voice"], (180, 6))[0] for c in hosts}
    low, high = min(pitch.values()), max(pitch.values())
    return [{"id": c["id"], "name": c["name"],
             "label": eleven.BY_ID[c["voice"]].short if eleven.is_eleven(c["voice"]) else None,
             "register": "deeper" if pitch[c["id"]] == low and low != high else (
                 "brighter" if pitch[c["id"]] == high and low != high else "middle")} for c in hosts]


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
async def site_config() -> dict:
    quizzes = await db.fetch("select slug, title, meeting_at from quizzes where status = 'open' order by created_at desc limit 12")
    return {"branding": await _branding_view(),
            "quizzes": [{"slug": q["slug"], "title": q["title"], "meetingAt": q["meeting_at"]} for q in quizzes]}


@router.get("/quiz/{slug}")
async def quiz_info(slug: str, player: dict | None = Depends(optional_player),
                    x_admin_token: str | None = Header(default=None)) -> dict:
    quiz = await find_quiz(slug, x_admin_token)
    rules = await settings_store.quiz_rules(quiz)
    stock = bank.counts(await bank.approved(quiz["id"], rules["question_sources"]))
    sections = await stats.askable_sections(quiz["id"])
    music = bool(rules["music_enabled"])
    view = {
        "branding": await _branding_view(),
        "quiz": {
            "slug": quiz["slug"], "title": quiz["title"], "description": quiz["description"],
            "meetingAt": quiz["meeting_at"], "status": quiz["status"], "requiresCode": bool(quiz["access_code"]),
            "teams": quiz["teams"], "sections": len(sections), "questions": stock["mcq"] + stock["fact_fake"],
            "ready": stock["mcq"] >= 3, "leaderboard": bool(rules["leaderboard"]),
            "modes": [{**settings_store.mode_view(m, rules), "music": music and m in ("show", "sudden_death", "jukebox")}
                      for m in rules["modes"]],
            "defaultMode": rules["default_mode"], "decades": rules["decades"] if music else [],
        },
        "player": None,
    }
    if player:
        view["player"] = await _player_view(player, quiz, rules)
    return view


async def _player_view(player: dict, quiz: dict, rules: dict) -> dict:
    unfinished = await db.fetchrow(
        "select id, mode from runs where player_id = $1 and quiz_id = $2 and status = 'in_progress'"
        " and started_at > now() - interval '2 hours' order by started_at desc limit 1", player["id"], quiz["id"])
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
    await find_quiz(slug, x_admin_token)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", host_id):
        raise HTTPException(404, "There is no such host.")
    try:
        found = await host_samples.sample(host_id)
    except Exception as exc:  # noqa: BLE001 – the voice services can be down; the lobby carries on without it
        _log.warning("host sample for %s failed: %s", host_id, exc)
        raise HTTPException(503, "That host cannot be heard right now. Try again in a minute.") from exc
    if found is None:
        raise HTTPException(404, "There is no such host.")
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
async def join(slug: str, body: JoinRequest, request: Request, x_admin_token: str | None = Header(default=None)) -> dict:
    throttle(request, "join", 600)      # a whole company can sit behind one address and join in the same minute
    quiz = await find_quiz(slug, x_admin_token)
    email = body.email.strip().lower()
    domains = [d.lower().lstrip("@") for d in quiz["allowed_email_domains"]]
    if domains and email.split("@")[-1] not in domains:
        raise HTTPException(403, f"Please use your work email ({', '.join('@' + d for d in domains)}).")
    if quiz["access_code"] and (body.accessCode or "").strip().lower() != quiz["access_code"].strip().lower():
        raise HTTPException(403, "That access code is not right. Check the invitation.")
    team = body.team if (body.team and (not quiz["teams"] or body.team in quiz["teams"])) else None
    if quiz["teams"] and not team:
        raise HTTPException(422, "Pick your team.")
    player = await upsert_player(body.name, email, team)
    rules = await settings_store.quiz_rules(quiz)
    return {"token": issue_player_token(player["id"]), "player": await _player_view(player, quiz, rules)}


@router.get("/quiz/{slug}/leaderboard")
async def quiz_leaderboard(slug: str, mode: str | None = None, team: str | None = None,
                           limit: int = 100, offset: int = 0, q: str | None = None,
                           player: dict = Depends(current_player),      # names and scores are for people who joined
                           x_admin_token: str | None = Header(default=None)) -> dict:
    quiz = await find_quiz(slug, x_admin_token)
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
    itemId: str
    choiceIndex: int | None = None


class WagerBody(BaseModel):
    itemId: str
    amount: int
    readAloud: bool = False      # the page shows the question while the host reads it, then asks for its answers


class LifelineBody(BaseModel):
    itemId: str
    kind: str


class ItemBody(BaseModel):
    itemId: str


def _game_error(exc: engine.GameError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _load_run(run_id: str, player: dict) -> tuple[dict, dict, dict]:
    run = await db.fetchrow("select * from runs where id = $1", run_id)
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
    throttle(request, "start", 600)
    quiz = await find_quiz(slug, x_admin_token)
    rules = await settings_store.quiz_rules(quiz)
    try:
        run = await engine.start_run(quiz, rules, player, body.mode or rules["default_mode"], body.decade)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return await _turn(run, rules, deal=False)     # the first question is dealt once the title card is up


@router.get("/runs/{run_id}")
async def resume_run(run_id: str, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    return await _turn(run, rules, deal=False)


@router.get("/runs/{run_id}/soundtrack")
async def run_soundtrack(run_id: str, player: dict = Depends(current_player)) -> dict:
    """Songs to play quietly behind this game (none for a Straight Quiz, or when music is switched off)."""
    run, _, rules = await _load_run(run_id, player)
    if not (rules["music_enabled"] and rules.get("soundtrack", True)) or run["mode"] == "straight":
        return {"decade": None, "tracks": []}
    decade = music_service.game_decade(run, rules)
    if not decade:
        return {"decade": None, "tracks": []}
    music_service.ensure_decade(decade)
    asked = await db.fetch("select music_track_id from run_items where run_id = $1 and music_track_id is not null", run_id)
    tracks = await music_service.soundtrack(decade, run["id"], {r["music_track_id"] for r in asked})
    tracks = favorites.mix(tracks, await favorites.for_player(player["id"]), run["id"], decade)
    return {"decade": decade, "tracks": tracks, "volume": rules["soundtrack_volume"], "mix": rules["mix"]}


@router.post("/runs/{run_id}/next")
async def next_turn(run_id: str, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    return await _turn(run, rules)


@router.post("/runs/{run_id}/open")
async def open_item(run_id: str, body: ItemBody, player: dict = Depends(current_player)) -> dict:
    """The title card is done: reveal a sealed item and start its clock."""
    run, _, rules = await _load_run(run_id, player)
    try:
        item, run = await engine.open_item(run, rules, body.itemId)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/answer")
async def answer(run_id: str, body: AnswerBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    try:
        reveal = await engine.submit_answer(run, rules, body.itemId, body.choiceIndex)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    item = await db.fetchrow("select question_id from run_items where id = $1", body.itemId)
    if item and item["question_id"]:
        await stats.note_question_health(item["question_id"])
    return reveal


@router.post("/runs/{run_id}/wager")
async def wager(run_id: str, body: WagerBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.place_wager(run, body.itemId, body.amount, read_aloud=body.readAloud)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/start-clock")
async def start_clock(run_id: str, body: ItemBody, player: dict = Depends(current_player)) -> dict:
    """The final question has been read out, or the player skipped the reading: show its answers, start its clock."""
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.start_clock(run, body.itemId)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/lifeline")
async def lifeline(run_id: str, body: LifelineBody, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    try:
        item = await engine.use_lifeline(run, body.itemId, body.kind, hint_writer=host_service.hint_text)
    except engine.GameError as exc:
        raise _game_error(exc) from exc
    run = await db.fetchrow("select * from runs where id = $1", run_id)
    return {"item": await engine.public_item(item, run), "state": engine.run_state(run, rules)}


@router.post("/runs/{run_id}/media-error")
async def media_error(run_id: str, body: ItemBody, player: dict = Depends(current_player)) -> dict:
    """The browser could not play a question's song (a music clip, or the song behind a jargon title). Work out whose
    fault that is, and carry on either way.

    If the clip really is gone it is retired and the question is dealt again with another song. If the
    clip is fine, the trouble is on the player's side (a network that blocks the music host, say):
    nothing is retired, and this game simply continues without music instead of failing every break.
    Office Jukebox is all music, but its jargon rounds still work as words: they stay, without their
    songs (and its decade stays, since the questions are about it); only its clip questions go.
    """
    run, _, rules = await _load_run(run_id, player)
    item = await db.fetchrow("select * from run_items where id = $1 and run_id = $2 and answered_at is null",
                             body.itemId, run_id)
    if item and item["slot"] in engine.CLIP_SLOTS:
        payload = item["payload"]
        attempt = int(payload.get("attempt", 1))
        clip_gone = await music_service.preview_is_dead(payload["preview_url"])
        if clip_gone:
            await music_service.retire_track(item["music_track_id"])
        if clip_gone and attempt < 3:
            slot = {"slot": item["slot"], "round": item["round_no"], "name": item["round_name"],
                    "limit": item["time_limit_sec"], "mult": float(item["points_multiplier"]), "attempt": attempt + 1}
            if payload.get("asks"):
                slot["asks"] = payload["asks"]           # "name the artist" stays "name the artist"
            await db.execute("delete from run_items where id = $1", item["id"])
            await db.execute("update runs set position = position - 1, plan = $2::jsonb || plan where id = $1",
                             run_id, [slot])
        elif run["mode"] == "jukebox":
            rest = [{**slot, "no_clip": True} if slot["slot"] in ("jargon", "song_or_jargon") else slot
                    for slot in run["plan"] if slot["slot"] != "music"]
            if item["slot"] == "jargon":                 # the title stays on screen, now without its song
                await db.execute("update run_items set payload = payload || '{\"no_clip\": true}'::jsonb where id = $1",
                                 item["id"])
                await db.execute("update runs set plan = $2 where id = $1", run_id, rest)
            else:
                await db.execute("delete from run_items where id = $1", item["id"])
                await db.execute("update runs set position = position - 1, plan = $2 where id = $1", run_id, rest)
        else:
            without_music = [slot for slot in run["plan"] if slot["slot"] != "music"]
            await db.execute("delete from run_items where id = $1", item["id"])
            await db.execute("update runs set position = position - 1, plan = $2, decade = null where id = $1",
                             run_id, without_music)
        run = await db.fetchrow("select * from runs where id = $1", run_id)
    return await _turn(run, rules)


@router.post("/runs/{run_id}/quit")
async def quit_run(run_id: str, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    run = await engine.finish_run(run, rules, abandoned=True)
    return {"summary": await stats.run_summary(run, rules)}


@router.get("/runs/{run_id}/summary")
async def summary(run_id: str, player: dict = Depends(current_player)) -> dict:
    run, _, rules = await _load_run(run_id, player)
    if run["status"] == "in_progress":
        raise HTTPException(409, "This game is still going.")
    return {"summary": await stats.run_summary(run, rules)}


class FlagBody(BaseModel):
    itemId: str
    reason: str | None = Field(default=None, max_length=300)


@router.post("/questions/flag")
async def flag_question(body: FlagBody, request: Request, player: dict = Depends(current_player)) -> dict:
    throttle(request, "flag", 40)
    item = await db.fetchrow("select question_id from run_items where id = $1 and player_id = $2", body.itemId, player["id"])
    if item is None or not item["question_id"]:
        raise HTTPException(404, "We could not find that question.")
    already = await db.fetchval("select 1 from question_flags where question_id = $1 and player_id = $2",
                                item["question_id"], player["id"])
    if not already:
        await db.execute("insert into question_flags (question_id, player_id, reason) values ($1,$2,$3)",
                         item["question_id"], player["id"], body.reason)
        await db.execute("update questions set flag_count = flag_count + 1 where id = $1", item["question_id"])
        await stats.note_question_health(item["question_id"])
    return {"ok": True}


class FeedbackBody(BaseModel):
    runId: str
    stars: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/feedback")
async def feedback(body: FeedbackBody, player: dict = Depends(current_player)) -> dict:
    """Rate a game. A player has one rating per game: tapping a different star replaces it."""
    run = await db.fetchrow("select id, quiz_id from runs where id = $1 and player_id = $2", body.runId, player["id"])
    if run is None:
        raise HTTPException(404, "We could not find that game.")
    await db.execute("insert into feedback (player_id, quiz_id, run_id, stars, comment) values ($1,$2,$3,$4,$5)"
                     " on conflict (player_id, run_id) where run_id is not null do update"
                     " set stars = excluded.stars, comment = coalesce(excluded.comment, feedback.comment), created_at = now()",
                     player["id"], run["quiz_id"], run["id"], body.stars, body.comment)
    return {"ok": True}
