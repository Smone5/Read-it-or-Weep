"""Live group play API: join with a code, follow the session, answer, and (host) drive it."""
from __future__ import annotations

import asyncio
import hmac
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

import db
from dependencies import current_player, optional_player
from routers.play import (
    _branding_view,
    _email_allowed,
    _is_admin,
    check_failed_code_throttle,
    find_quiz,
    has_quiz_access,
    merged_grants,
    record_failed_code,
    record_quiz_membership,
    require_quiz_access,
    throttle,
    upsert_player,
)
from security import issue_player_token
from services import live, settings_store

router = APIRouter(prefix="/api/live", tags=["live"])
_MAX_SSE_SECONDS = 45 * 60


def _error(exc: live.LiveError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _session(code: str) -> dict:
    try:
        return await live.load(code)
    except live.LiveError as exc:
        raise _error(exc) from exc


@router.get("/{code}/info")
async def info(code: str, request: Request) -> dict:
    throttle(request, "live-info", 600)
    session = await _session(code)
    quiz = await db.fetchrow(
        "select title, teams, access_code, allowed_email_domains from quizzes where id = $1",
        session["quiz_id"],
    )
    return {
        "code": session["code"], "status": session["status"], "quizTitle": quiz["title"],
        "teams": quiz["teams"], "requiresCode": bool(quiz["access_code"]),
        "branding": await _branding_view(),
    }


class LiveJoin(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    email: EmailStr | None = None
    team: str | None = Field(default=None, max_length=60)
    accessCode: str | None = Field(default=None, max_length=40)


@router.post("/{code}/join")
async def join(code: str, body: LiveJoin, request: Request,
               player: dict | None = Depends(optional_player),
               x_admin_token: str | None = Header(default=None)) -> dict:
    throttle(request, "live-join", 600)
    session = await _session(code)
    quiz = await db.fetchrow("select * from quizzes where id = $1", session["quiz_id"])
    admin = await _is_admin(x_admin_token)
    token = None
    existing_player = player
    if player is None:
        if not body.name or not body.email:
            raise HTTPException(422, "Enter your name and work email to join.")
        if not admin and not _email_allowed(quiz, body.email):
            raise HTTPException(403, "That email address is not eligible for this live game. Use your work email.")
        if quiz["access_code"] and not admin:
            check_failed_code_throttle(request, f"live-code:{quiz['id']}", 20)
            if not hmac.compare_digest((body.accessCode or "").strip().lower(), quiz["access_code"].strip().lower()):
                record_failed_code(request, f"live-code:{quiz['id']}")
                raise HTTPException(403, "That access code is not right. Check the invitation.")
        team = body.team if body.team and (not quiz["teams"] or body.team in quiz["teams"]) else None
        player = await upsert_player(body.name, body.email, team)
        grants = merged_grants(None, player["id"], quiz, grant_access=bool(quiz["access_code"]))
        token = issue_player_token(player["id"], quiz_grants=grants)
        player = {**player, "_quiz_grants": grants}
    else:
        if not admin and not _email_allowed(quiz, player["email"]):
            raise HTTPException(403, "That email address is not eligible for this live game. Use your work email.")
        already_member = admin or await has_quiz_access(quiz, player)
        if quiz["access_code"] and not already_member:
            check_failed_code_throttle(request, f"live-code:{quiz['id']}", 20)
            if not hmac.compare_digest((body.accessCode or "").strip().lower(), quiz["access_code"].strip().lower()):
                record_failed_code(request, f"live-code:{quiz['id']}")
                raise HTTPException(403, "Enter the quiz access code to join this live room.")
            grants = merged_grants(existing_player, player["id"], quiz, grant_access=True)
            token = issue_player_token(player["id"], quiz_grants=grants)
            player = {**player, "_quiz_grants": grants}
    # Ensure a returning player's team matches this quiz's configured team list rather than leaking an old quiz's team.
    session_player = dict(player)
    if quiz["teams"]:
        chosen_team = body.team if body.team and body.team in quiz["teams"] else (
            player.get("team") if player.get("team") in quiz["teams"] else None
        )
        session_player["team"] = chosen_team
    elif body.team:
        session_player["team"] = body.team[:60]
    await record_quiz_membership(quiz["id"], player["id"])
    try:
        await live.join(session, session_player)
        session = await live.load(code, fresh=True)
        return {"token": token, "state": await live.state(session, host=False, player=player)}
    except live.LiveError as exc:
        raise _error(exc) from exc


async def _require_live_viewer(session: dict, player: dict | None, x_host_key: str | None) -> bool:
    if live.is_host(session, x_host_key):
        return True
    if player is not None and await live.is_participant(session["id"], player["id"]):
        return False
    raise HTTPException(403, "Join the live game first to view its questions.")


@router.get("/{code}/state")
async def get_state(code: str, request: Request, player: dict | None = Depends(optional_player),
                    x_host_key: str | None = Header(default=None)) -> dict:
    throttle(request, "live-state", 600)
    session = await _session(code)
    host = await _require_live_viewer(session, player, x_host_key)
    return await live.state(session, host=host, player=player)


@router.get("/{code}/events")
async def events(code: str, request: Request, player: dict | None = Depends(optional_player),
                 x_host_key: str | None = Header(default=None)) -> StreamingResponse:
    throttle(request, "live-events", 600)
    session = await _session(code)
    host = await _require_live_viewer(session, player, x_host_key)

    async def stream():
        last_version, last_sent = None, 0.0
        started = time.monotonic()
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            now = time.monotonic()
            if now - started > _MAX_SSE_SECONDS:
                break
            try:
                current = await live.tick(await live.load(code))
            except live.LiveError:
                break
            # Re-check host key so rotating/reclaiming a session disconnects revoked hosts immediately.
            if host and not live.is_host(current, x_host_key):
                break
            live_count = host and current["status"] == "question" and now - last_sent >= 1.0
            if current["version"] != last_version or live_count:
                view = await live.state(current, host=host, player=player)
                yield f"data: {json.dumps(view, default=str)}\n\n"
                last_version, last_sent = current["version"], now
            elif now - last_sent >= 15:
                yield ": keep-alive\n\n"
                last_sent = now
            if current["status"] == "finished" and last_version == current["version"] and now - last_sent > 30:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class LiveAnswer(BaseModel):
    choiceIndex: int
    questionIndex: int | None = None


@router.post("/{code}/answer")
async def answer(code: str, body: LiveAnswer, request: Request, player: dict | None = Depends(optional_player)) -> dict:
    throttle(request, "live-answer", 1000)
    if player is None:
        raise HTTPException(401, "Join the game first.")
    try:
        return await live.answer(await _session(code), player, body.choiceIndex, expected_index=body.questionIndex)
    except live.LiveError as exc:
        raise _error(exc) from exc


class HostLiveBody(BaseModel):
    decade: str | None = None


@router.post("/quiz/{slug}/host")
async def host_live(slug: str, body: HostLiveBody, request: Request,
                    player: dict = Depends(current_player),
                    x_admin_token: str | None = Header(default=None)) -> dict:
    """Let an authorized player host a live group room directly from an open quiz's lobby."""
    throttle(request, "live-host", 20)
    admin = await _is_admin(x_admin_token)
    quiz = await find_quiz(slug, x_admin_token)
    await require_quiz_access(quiz, player, is_admin=admin)
    try:
        return await live.create_session(quiz, await settings_store.quiz_rules(quiz), body.decade)
    except live.LiveError as exc:
        raise _error(exc) from exc


class Control(BaseModel):
    action: str
    expectedStatus: str | None = None
    expectedIndex: int | None = None


@router.post("/{code}/control")
async def control(code: str, body: Control, request: Request, x_host_key: str | None = Header(default=None)) -> dict:
    throttle(request, "live-control", 120)
    session = await _session(code)
    if not live.is_host(session, x_host_key):
        raise HTTPException(403, "Only the host screen can control the game.")
    try:
        session = await live.control(
            session, body.action,
            expected_status=body.expectedStatus, expected_index=body.expectedIndex,
        )
        return await live.state(session, host=True, player=None)
    except live.LiveError as exc:
        raise _error(exc) from exc


@router.post("/{code}/rematch")
async def rematch(code: str, request: Request, x_host_key: str | None = Header(default=None)) -> dict:
    """Start a fresh live session of the same quiz directly from the host's Finale screen."""
    throttle(request, "live-host", 20)
    session = await _session(code)
    if not live.is_host(session, x_host_key):
        raise HTTPException(403, "Only the host screen can start a rematch.")
    quiz = await db.fetchrow("select * from quizzes where id = $1", session["quiz_id"])
    if quiz is None:
        raise HTTPException(404, "Quiz not found.")
    try:
        return await live.create_session(quiz, await settings_store.quiz_rules(quiz), session["settings"].get("decade"))
    except live.LiveError as exc:
        raise _error(exc) from exc
