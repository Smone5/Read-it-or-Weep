"""Live group play API: join with a code, follow the session, answer, and (host) drive it."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

import db
from dependencies import current_player, optional_player
from routers.play import _branding_view, find_quiz, throttle, upsert_player
from security import issue_player_token
from services import live, settings_store

router = APIRouter(prefix="/api/live", tags=["live"])


def _error(exc: live.LiveError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _session(code: str) -> dict:
    try:
        return await live.load(code)
    except live.LiveError as exc:
        raise _error(exc) from exc


@router.get("/{code}/info")
async def info(code: str) -> dict:
    session = await _session(code)
    quiz = await db.fetchrow("select title, teams, allowed_email_domains from quizzes where id = $1", session["quiz_id"])
    return {"code": session["code"], "status": session["status"], "quizTitle": quiz["title"], "teams": quiz["teams"],
            "branding": await _branding_view()}


class LiveJoin(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    email: EmailStr | None = None
    team: str | None = Field(default=None, max_length=60)


@router.post("/{code}/join")
async def join(code: str, body: LiveJoin, request: Request, player: dict | None = Depends(optional_player)) -> dict:
    throttle(request, "live-join", 600)
    session = await _session(code)
    token = None
    if player is None:
        if not body.name or not body.email:
            raise HTTPException(422, "Enter your name and work email to join.")
        quiz = await db.fetchrow("select teams, allowed_email_domains from quizzes where id = $1", session["quiz_id"])
        domains = [d.lower().lstrip("@") for d in quiz["allowed_email_domains"]]
        if domains and body.email.lower().split("@")[-1] not in domains:
            raise HTTPException(403, f"Please use your work email ({', '.join('@' + d for d in domains)}).")
        team = body.team if body.team and (not quiz["teams"] or body.team in quiz["teams"]) else None
        player = await upsert_player(body.name, body.email, team)
        token = issue_player_token(player["id"])
    try:
        await live.join(session, player)
        session = await live.load(code, fresh=True)
        return {"token": token, "state": await live.state(session, host=False, player=player)}
    except live.LiveError as exc:
        raise _error(exc) from exc


@router.get("/{code}/state")
async def get_state(code: str, player: dict | None = Depends(optional_player),
                    x_host_key: str | None = Header(default=None)) -> dict:
    session = await _session(code)
    return await live.state(session, host=live.is_host(session, x_host_key), player=player)


@router.get("/{code}/events")
async def events(code: str, request: Request, player: dict | None = Depends(optional_player),
                 x_host_key: str | None = Header(default=None)) -> StreamingResponse:
    session = await _session(code)
    host = live.is_host(session, x_host_key)

    async def stream():
        last_version, last_sent = None, 0.0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            try:
                current = await live.tick(await live.load(code))
            except live.LiveError:
                break
            now = time.monotonic()
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


@router.post("/{code}/answer")
async def answer(code: str, body: LiveAnswer, player: dict | None = Depends(optional_player)) -> dict:
    if player is None:
        raise HTTPException(401, "Join the game first.")
    try:
        return await live.answer(await _session(code), player, body.choiceIndex)
    except live.LiveError as exc:
        raise _error(exc) from exc


class HostLiveBody(BaseModel):
    decade: str | None = None


@router.post("/quiz/{slug}/host")
async def host_live(slug: str, body: HostLiveBody, request: Request,
                    player: dict = Depends(current_player)) -> dict:
    """Let a signed-in player host a live group room directly from an open quiz's lobby."""
    throttle(request, "live-host", 20)
    quiz = await find_quiz(slug)
    try:
        return await live.create_session(quiz, await settings_store.quiz_rules(quiz), body.decade)
    except live.LiveError as exc:
        raise _error(exc) from exc


class Control(BaseModel):
    action: str


@router.post("/{code}/control")
async def control(code: str, body: Control, x_host_key: str | None = Header(default=None)) -> dict:
    session = await _session(code)
    if not live.is_host(session, x_host_key):
        raise HTTPException(403, "Only the host screen can control the game.")
    try:
        session = await live.control(session, body.action)
        return await live.state(session, host=True, player=None)
    except live.LiveError as exc:
        raise _error(exc) from exc
