"""ElevenLabs: the voices of the host.

The host can be performed two ways. With one of OpenAI's voices, a single realtime model writes and
speaks each line (host_service.HostVoice). With an ElevenLabs voice the work is split, the way a show
splits it between a writer and a performer: a text model writes the line and marks up how each beat
is to be played with a short tag in square brackets ("[amused]", "[building]"), and ElevenLabs
performs it (host_service.WrittenHostVoice).

Why: the realtime voices were judged by ear and rejected ("missing diction and tone"). Direction beat
by beat was what made a line sound acted rather than read, and on independent blind-listening tests
ElevenLabs' voices rank far above the realtime model's.

Only the server talks to ElevenLabs. The key never reaches a browser, and the browser hears the same
24 kHz, 16-bit mono audio it always has. Without a key, an ElevenLabs voice is played by the OpenAI
voice closest to it, so the host never goes silent for want of one.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx
import websockets

from config import config

log = logging.getLogger("eleven")

API = "https://api.elevenlabs.io"
SOCKET = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
OUTPUT_FORMAT = "pcm_24000"          # what the browser already plays: 24 kHz, 16-bit mono
FIRST_SOUND_TIMEOUT_SEC = 8.0        # no audio by now: give up on this line and use the backup voice
CHUNK_TIMEOUT_SEC = 6.0              # silence in the middle of a line this long means the stream is stuck
REFUSED = (401, 402, 403, 404)       # a wrong key, no credit, no access, or no such voice: no point retrying

# Which ElevenLabs path performs a line. "socket": Eleven v4 Turbo over the text-to-dialogue WebSocket, the
# fast model (about 0.2 s to first sound). "dialogue": Eleven v4 over HTTP, slower to start, the richest
# performance. "tts": the plain text-to-speech endpoint, with whichever model ELEVENLABS_MODEL names.
DEFAULT_MODEL = {"socket": "eleven_v4_turbo", "dialogue": "eleven_v4", "tts": "eleven_v4"}


@dataclass(frozen=True)
class Voice:
    id: str           # how the game stores it: "eleven:classic"
    label: str        # the admin's voice list
    blurb: str        # one line about it, for the admin
    short: str        # two or three words, for the lobby's "choose your host" buttons
    voice_id: str     # ElevenLabs' own id
    owner_id: str     # the account it is shared from
    fallback: str     # the OpenAI voice that stands in when ElevenLabs cannot be used
    name: str         # its name on the ElevenLabs page, which it keeps when added to an account

    @property
    def share_link(self) -> str:
        """Where an organiser adds it to their own ElevenLabs account ("Add to My Voices")."""
        return f"https://elevenlabs.io/app/voice-lab/share/{self.owner_id}/{self.voice_id}"


# The "game show host" voices of the ElevenLabs voice library (elevenlabs.io/voice-library/game-show-host),
# chosen by ear by the organiser. Ids are from the page's share links. They are not in the searchable library,
# and the API will only use a voice that is in the account's own voices: each must be added once, through its
# share link, in the ElevenLabs app. A voice added that way gets an id of its own; account_voice_id() finds it.
_LIBRARY = "779d91788b0d9e364d32e0a0e4273ec722b92887fa710d82f50b2d034a9fb97b"
VOICES: tuple[Voice, ...] = (
    Voice("eleven:classic", "Classic game-show host", "Energetic American broadcast host, late 30s: polished and theatrical.",
          "Classic host", "5XbWatCr4Q4ezek0ZhfT", _LIBRARY, "cedar", "The Classic Game Show Host"),
    Voice("eleven:british", "Sophisticated British host", "Smooth and witty, early 30s, a slight British accent.",
          "British wit", "whydKScabPmVENfyGF8q", _LIBRARY, "coral", "The Sophisticated British Host"),
    Voice("eleven:southern", "Southern charmer", "Rich and sassy, mid-40s, a vibrant Southern American accent.",
          "Southern sass", "jphAGjEkmAlXu5DE3FmO", _LIBRARY, "marin", "The Southern Charmer"),
    Voice("eleven:hype", "Modern hype master", "Fast and hyped, late 20s, American with an urban flair.",
          "Hype master", "DhaeydPYLLVPtHkt04Qp", _LIBRARY, "verse", "The Modern Hype Master"),
)
BY_ID: dict[str, Voice] = {voice.id: voice for voice in VOICES}


class ElevenError(RuntimeError):
    """ElevenLabs refused or failed. The caller carries on with the backup voice.

    ``permanent``: it will not work on a second try either (a wrong key, no credit left, a plan without access).
    """

    def __init__(self, message: str, *, permanent: bool = False, busy: bool | None = None):
        super().__init__(message)
        self.permanent = permanent
        # ElevenLabs is only busy (too many lines at once for the plan, or a moment's rate limit): worth another try.
        self.busy = busy if busy is not None else _sounds_busy(message)


def _sounds_busy(message: str) -> bool:
    text = message.lower()
    return any(word in text for word in ("429", "concurrent", "too many", "rate limit", "busy", "system_busy"))


def configured() -> bool:
    return bool(config.elevenlabs_api_key)


def is_eleven(voice: str | None) -> bool:
    return bool(voice) and voice in BY_ID


def engine() -> str:
    chosen = (config.elevenlabs_engine or "socket").lower()
    return chosen if chosen in DEFAULT_MODEL else "socket"


def model() -> str:
    return config.elevenlabs_model or DEFAULT_MODEL[engine()]


def _headers() -> dict[str, str]:
    return {"xi-api-key": config.elevenlabs_api_key}


# --------------------------------------------------------------------------- which id the account knows a voice by

_account_ids: dict[str, str] = {}
_looked_at = 0.0
_listed = False                # the account's voices have been read at least once
LOOK_AGAIN_SEC = 60.0          # a voice that was missing is looked for again after this (an organiser may add it)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match_account_voices(items: list[dict]) -> dict[str, str]:
    """Pair our voices with the account's own (the items of GET /v1/voices): by id, by the id of the voice
    it was saved from, or by name."""
    found: dict[str, str] = {}
    for voice in VOICES:
        for item in items:
            original = (item.get("sharing") or {}).get("original_voice_id")
            if voice.voice_id in (item.get("voice_id"), original):
                found[voice.id] = item["voice_id"]
                break
        else:
            named = [item for item in items if _plain(voice.name) in _plain(item.get("name", ""))]
            if named:
                found[voice.id] = named[0]["voice_id"]
    return found


async def _look() -> None:
    """Read the account's voices again - at most once a minute, however often it is asked."""
    global _looked_at, _listed
    if time.monotonic() - _looked_at <= LOOK_AGAIN_SEC:
        return
    _looked_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{API}/v1/voices", headers=_headers())
    except httpx.HTTPError as exc:
        log.warning("could not list the ElevenLabs account's voices (%s)", type(exc).__name__)
        return
    if response.status_code != 200:
        log.warning("could not list the ElevenLabs account's voices: %s", _complaint(response.status_code, response.content))
        return
    _account_ids.clear()
    _account_ids.update(match_account_voices(response.json().get("voices", [])))
    _listed = True


async def account_voice_id(voice: Voice) -> str:
    """The id this account uses for one of our voices (the page's own id if the account does not have it,
    which ElevenLabs then answers with "voice not found")."""
    if voice.id not in _account_ids:
        await _look()
    return _account_ids.get(voice.id, voice.voice_id)


async def voices_in_account() -> dict[str, bool] | None:
    """Which of our voices the account has (None: no key, or the list could not be read)."""
    if not configured():
        return None
    await _look()
    return {voice.id: voice.id in _account_ids for voice in VOICES} if _listed else None


def _complaint(status: int, body: bytes | str) -> str:
    """ElevenLabs' own explanation of a refusal, short, without anything that could be a secret."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        detail = json.loads(text).get("detail", text)
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status") or json.dumps(detail)
        text = str(detail)
    except (ValueError, AttributeError):
        pass
    return f"ElevenLabs answered {status}: {text[:200]}"


# --------------------------------------------------------------------------- performing a line

BUSY_RETRIES = 3
_slots: asyncio.Semaphore | None = None


def _slot() -> asyncio.Semaphore:
    """How many lines go to ElevenLabs at the same moment (ELEVENLABS_CONCURRENCY: the plan's limit)."""
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(max(1, config.elevenlabs_concurrency))
    return _slots


async def perform(text: str, voice: Voice, *, output_format: str = OUTPUT_FORMAT) -> AsyncIterator[bytes]:
    """The line, performed: 24 kHz 16-bit mono PCM (or ``output_format``, e.g. "mp3_44100_64"), in pieces, as it
    is made.

    Raises ElevenError when ElevenLabs cannot be used (no key, refused, too slow to start).
    """
    if not configured():
        raise ElevenError("no ElevenLabs key is set", permanent=True)
    path = engine()
    voice_id = await account_voice_id(voice)
    # Several games at once: the plan takes only so many lines at the same moment. The rest queue here for a free
    # slot, and a "busy" answer is tried again after a short pause rather than handed to the backup voice at once.
    for attempt in range(BUSY_RETRIES + 1):
        started = False
        async with _slot():
            pieces = (_socket(text, voice_id, output_format) if path == "socket"
                      else _http(text, voice_id, path, output_format))
            try:
                async with contextlib.aclosing(pieces) as audio:
                    async for chunk in audio:
                        started = True
                        yield chunk
                return
            except ElevenError as exc:
                if started or not exc.busy or attempt == BUSY_RETRIES:
                    raise
                log.info("ElevenLabs busy (%s); trying again", exc)
        await asyncio.sleep(0.4 * (attempt + 1) + random.random() * 0.3)


async def _socket(text: str, voice_id: str, output_format: str = OUTPUT_FORMAT) -> AsyncIterator[bytes]:
    """Eleven v4 Turbo over the text-to-dialogue WebSocket: send the line, then ask for the rest at once."""
    query = urlencode({"model_id": model(), "output_format": output_format})
    try:
        connection = await asyncio.wait_for(
            websockets.connect(f"{SOCKET}?{query}", additional_headers=_headers(), max_size=2 ** 22, open_timeout=6),
            timeout=7)
    except Exception as exc:  # noqa: BLE001 – refused, unreachable, or the key is wrong
        status = getattr(getattr(exc, "response", None), "status_code", None)
        body = getattr(getattr(exc, "response", None), "body", b"") or b""
        raise ElevenError(_complaint(status, body) if status else f"could not reach ElevenLabs ({type(exc).__name__})",
                          permanent=status in REFUSED, busy=status == 429 or None) from exc
    try:
        await connection.send(json.dumps({"voices": [voice_id]}))
        await connection.send(json.dumps({"inputs": [{"text": text, "voice_id": voice_id, "new_turn": False}]}))
        await connection.send(json.dumps({"close_socket": True}))       # flush what is buffered, then finish
        heard = False
        while True:
            wait = CHUNK_TIMEOUT_SEC if heard else FIRST_SOUND_TIMEOUT_SEC
            try:
                raw = await asyncio.wait_for(connection.recv(), timeout=wait)
            except websockets.ConnectionClosedOK:
                return
            except websockets.ConnectionClosed as exc:
                if heard:
                    return                                   # it closed after the line: that is the end of it
                raise ElevenError(f"ElevenLabs closed the connection ({exc.rcvd.code if exc.rcvd else 'no code'}: "
                                  f"{(exc.rcvd.reason if exc.rcvd else '')[:160]})") from exc
            except asyncio.TimeoutError as exc:
                raise ElevenError("ElevenLabs took too long") from exc
            if isinstance(raw, bytes):                       # raw audio, should it ever come that way
                heard = True
                yield raw
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            audio = message.get("audio") or message.get("audio_base_64") or message.get("audio_base64")
            if audio:
                heard = True
                yield base64.b64decode(audio)
            elif message.get("error") or (message.get("message") and not message.get("is_final")):
                problem = str(message.get("error") or message.get("message"))[:200]
                raise ElevenError(f"ElevenLabs: {problem}", permanent=any(
                    word in problem.lower() for word in ("voice_not_found", "unauthorized", "quota", "permission")))
            if message.get("is_final") or message.get("isFinal"):
                return
    finally:
        with contextlib.suppress(Exception):
            await connection.close()


async def _http(text: str, voice_id: str, path: str, output_format: str = OUTPUT_FORMAT) -> AsyncIterator[bytes]:
    """Eleven v4 (or another model) over HTTP, streamed."""
    if path == "dialogue":
        url, body = f"{API}/v1/text-to-dialogue/stream", {"inputs": [{"text": text, "voice_id": voice_id}],
                                                          "model_id": model()}
    else:
        url, body = f"{API}/v1/text-to-speech/{voice_id}/stream", {"text": text, "model_id": model()}
    timeout = httpx.Timeout(connect=6.0, read=CHUNK_TIMEOUT_SEC, write=6.0, pool=6.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("POST", url, params={"output_format": output_format}, json=body,
                                     headers=_headers()) as response:
                if response.status_code != 200:
                    raise ElevenError(_complaint(response.status_code, await response.aread()),
                                      permanent=response.status_code in REFUSED, busy=response.status_code == 429 or None)
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise ElevenError(f"could not reach ElevenLabs ({type(exc).__name__})") from exc


# --------------------------------------------------------------------------- the account

async def save_to_account(voice: Voice) -> str:
    """Add a library voice to the account's own voices (some plans need that before the API will use it).

    Returns the voice id to use from now on.
    """
    url = f"{API}/v1/voices/add/{voice.owner_id}/{voice.voice_id}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json={"new_name": voice.label}, headers=_headers())
    if response.status_code != 200:
        raise ElevenError(_complaint(response.status_code, response.content))
    return str(response.json().get("voice_id") or voice.voice_id)


async def account() -> dict:
    """What the plan allows and what has been used (needs a key allowed to read the user's details)."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{API}/v1/user/subscription", headers=_headers())
    if response.status_code != 200:
        raise ElevenError(_complaint(response.status_code, response.content))
    data = response.json()
    return {key: data.get(key) for key in ("tier", "character_count", "character_limit", "next_character_count_reset_unix",
                                           "status")}


_share: tuple[float, float | None] = (0.0, None)      # (when read, share of the plan used)
SHARE_FRESH_SEC = 300


async def share_used() -> float | None:
    """How much of the month's plan has been used, 0 to 1 (re-read every few minutes; None if it cannot be read)."""
    global _share
    now = time.monotonic()
    if _share[1] is not None and now - _share[0] < SHARE_FRESH_SEC:
        return _share[1]
    try:
        info = await account()
        limit = int(info.get("character_limit") or 0)
        share = int(info.get("character_count") or 0) / limit if limit else None
    except Exception:  # noqa: BLE001 - not knowing is never a reason to stop the host
        share = None
    _share = (now, share)
    return share
