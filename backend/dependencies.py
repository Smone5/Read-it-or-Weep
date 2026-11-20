"""Request dependencies: who is calling."""
from __future__ import annotations

from fastapi import Header, HTTPException, status

import db
from security import TokenError, read_admin_token, read_player_token


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def current_player(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Join the quiz first.")
    try:
        player_id = read_player_token(token)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    player = await db.fetchrow("select * from players where id = $1", player_id)
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Join the quiz first.")
    return player


async def optional_player(authorization: str | None = Header(default=None)) -> dict | None:
    token = _bearer(authorization)
    if not token:
        return None
    try:
        return await db.fetchrow("select * from players where id = $1", read_player_token(token))
    except TokenError:
        return None


async def current_admin(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in as an admin.")
    try:
        admin_id = read_admin_token(token)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    admin = await db.fetchrow("select id, email, name from admin_users where id = $1", admin_id)
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in as an admin.")
    return admin
