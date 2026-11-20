"""Signing and hashing helpers.

Everything is derived from APP_SECRET, so there is no dependency on any
identity service: player sessions, admin sessions, image links and live-game
host keys all work the same on every cloud.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from config import config

_ALGORITHM = "HS256"
PLAYER_TOKEN_DAYS = 90
ADMIN_TOKEN_HOURS = 12

_hasher = PasswordHasher()


class TokenError(Exception):
    pass


def _issue(kind: str, subject: str, ttl_seconds: int, **claims: Any) -> str:
    now = int(time.time())
    payload = {"sub": subject, "typ": kind, "iat": now, "exp": now + ttl_seconds, **claims}
    return jwt.encode(payload, config.app_secret, algorithm=_ALGORITHM)


def _read(token: str, kind: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, config.app_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Session expired.") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid session.") from exc
    if payload.get("typ") != kind or not payload.get("sub"):
        raise TokenError("Invalid session.")
    return payload


def issue_player_token(player_id: str) -> str:
    return _issue("player", player_id, PLAYER_TOKEN_DAYS * 86400)


def read_player_token(token: str) -> str:
    return _read(token, "player")["sub"]


def issue_admin_token(admin_id: str) -> str:
    return _issue("admin", admin_id, ADMIN_TOKEN_HOURS * 3600)


def read_admin_token(token: str) -> str:
    return _read(token, "admin")["sub"]


# --- passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --- signed, expiring links to report images -----------------------------------

def _media_signature(image_id: str, expires: int) -> str:
    message = f"media:{image_id}:{expires}".encode()
    return hmac.new(config.app_secret.encode(), message, hashlib.sha256).hexdigest()[:40]


def sign_media(image_id: str, ttl_seconds: int = 6 * 3600) -> tuple[int, str]:
    expires = int(time.time()) + ttl_seconds
    return expires, _media_signature(image_id, expires)


def verify_media(image_id: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(_media_signature(image_id, expires), signature or "")


# --- live-game host keys and join codes ---------------------------------------

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: easy to read off a screen


def new_join_code(length: int = 5) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def new_host_key() -> str:
    return secrets.token_urlsafe(24)


def hash_key(value: str) -> str:
    return hashlib.sha256(f"{config.app_secret}:{value}".encode()).hexdigest()


def key_matches(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_key(value or ""), expected_hash or "")
