"""A short spoken introduction from each host character, so players can hear the hosts before choosing.

Each sample is the character introducing themselves in their own voice and manner, written and
performed exactly as the game's lines are. It is recorded the first time anyone asks and kept in the
database, so playing it again - for every player, on every server - costs nothing. Its key is a
fingerprint of everything that shapes it, so changing a character (or adding an ElevenLabs key)
records a new one.

Stored as MP3 when ElevenLabs (or the backup voice) performs it - about 50 KB, which matters on a
phone - and as WAV for the OpenAI realtime voices, which only give raw audio.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import db
import llm
from config import config
from services import eleven, host_service, settings_store

log = logging.getLogger("host.samples")

VERSION = 3                     # change to record every sample again (e.g. after rewording INTRO)
SAMPLE_FORMAT = "mp3_44100_64"  # ElevenLabs' compact MP3
# A pitch, not a gag: a player choosing a host wants to know what a game with this one will be like. Each host
# is also shown what the others said, so that four hosts heard side by side do not sound alike. (Earlier
# wordings gave two hosts the same career-choice joke, then two near-identical "I'll treat you kindly" lines.)
INTRO = ("A contestant is choosing which host they want for their game, and has tapped to hear you. Introduce "
         "yourself in one breath: your name, then ONE line that makes them want you as their host - what a game with "
         "you will be like, in your own style. Under 20 spoken words. No verdict, nothing about any answer, no question "
         "back to them, and no joke about your career or the show's budget.")

_locks: dict[str, asyncio.Lock] = {}
_warming: set[str] = set()
_background: set[asyncio.Task] = set()


async def _setup(host_id: str) -> tuple[dict, dict, dict] | None:
    """The host settings, the character with this id (None if there is none), and the branding."""
    settings = await settings_store.get("host")
    character = next((c for c in settings["hosts"] if c["id"] == host_id), None)
    if character is None:
        return None
    return settings, character, await settings_store.get("branding")


def sample_key(character: dict, settings: dict, branding: dict) -> tuple[str, dict]:
    """The fingerprint of a sample, and how its character will be performed."""
    performer = host_service.resolve_voice(character["voice"])
    shape = {"v": VERSION, "id": character["id"], "name": character["name"], "voice": character["voice"],
             "manner": character.get("manner") or host_service.DEFAULT_MANNER, "style": character.get("style") or "",
             "sarcasm": int(settings["sarcasm"]), "game": branding["game_title"], "company": branding["company_name"],
             "pace": settings.get("pace", "snappy"), "quality": settings.get("voice_quality"), **performer}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:32], performer


def _brief(others: list[str]) -> str:
    """INTRO, plus what the other hosts said, so this one sounds nothing like them."""
    if not others:
        return INTRO
    return (INTRO + "\nThe other hosts introduced themselves like this. Sound nothing like them - not their words, "
            "not their shape:\n" + "\n".join(f"- \"{line}\"" for line in others))


async def _record(character: dict, settings: dict, branding: dict, performer: dict,
                  others: list[str] | None = None) -> tuple[str, bytes, str]:
    """Write and perform the introduction. Returns (mime type, audio, the words as heard)."""
    manner = character.get("manner") or host_service.DEFAULT_MANNER
    persona = host_service.build_persona(
        name=character["name"], game_title=branding["game_title"], company=branding["company_name"],
        sarcasm=int(settings["sarcasm"]), style=character.get("style") or None, manner=manner,
        tags=performer["engine"] == "eleven")
    if performer["engine"] == "eleven":
        line = await host_service.write_line(persona, [{"role": "user", "content": _brief(others or [])}])
        words = host_service.spoken_text(line)
        voice = eleven.BY_ID[performer["voice"]]
        try:
            audio = b"".join([chunk async for chunk in eleven.perform(line, voice, output_format=SAMPLE_FORMAT)])
            return "audio/mpeg", audio, words
        except eleven.ElevenError as exc:
            log.warning("ElevenLabs could not record %s's sample (%s); the backup voice does", character["name"], exc)
        return "audio/mpeg", await _backup_mp3(words, voice.fallback, manner), words
    host = {**settings, "manner": manner, **performer}
    pcm, words = bytearray(), ""
    async with host_service.open_voice(persona, host, True) as session:
        async for kind, data in session.say(_brief(others or [])):
            if kind == "audio":
                pcm.extend(data)  # type: ignore[arg-type]
            else:
                words += str(data)
    return "audio/wav", host_service.wav_file(bytes(pcm)), " ".join(words.split())


async def _backup_mp3(words: str, fallback: str, manner: str) -> bytes:
    """The OpenAI voice that stands in for an ElevenLabs one, as MP3."""
    speech_voice = host_service._SPEECH_VOICE.get(fallback, fallback)
    async with llm.client().audio.speech.with_streaming_response.create(
            model=config.model_speech, voice=speech_voice, input=words,
            instructions=host_service.MANNERS.get(manner, host_service.MANNERS[host_service.DEFAULT_MANNER])["sound"],
            response_format="mp3") as response:
        return b"".join([chunk async for chunk in response.iter_bytes()])


async def sample(host_id: str) -> dict | None:
    """The sample for one host character: recorded now if it does not exist yet. None if there is no such host
    or the host does not speak."""
    setup = await _setup(host_id)
    if setup is None:
        return None
    settings, character, branding = setup
    if not settings["voice_enabled"]:
        return None
    key, performer = sample_key(character, settings, branding)
    row = await db.fetchrow("select mime, audio, words from host_samples where key = $1", key)
    if row is None:
        async with _locks.setdefault(key, asyncio.Lock()):
            row = await db.fetchrow("select mime, audio, words from host_samples where key = $1", key)
            if row is None:
                others = [r["words"] for r in await db.fetch(
                    "select words from host_samples where host_id <> $1 order by created_at", host_id)]
                mime, audio, words = await _record(character, settings, branding, performer, others)
                if len(audio) < 2000:
                    raise RuntimeError("the sample came back empty")
                await db.execute("insert into host_samples (key, host_id, words, mime, audio) values ($1,$2,$3,$4,$5)"
                                 " on conflict (key) do nothing", key, host_id, words, mime, audio)
                await db.execute("delete from host_samples where host_id = $1 and key <> $2", host_id, key)
                row = {"mime": mime, "audio": audio, "words": words}
        _locks.pop(key, None)
    return {"key": key, "mime": row["mime"], "audio": bytes(row["audio"]), "words": row["words"]}


async def warm() -> None:
    """Record any sample that is missing (in the background: players then never wait for one)."""
    settings = await settings_store.get("host")
    if not settings["voice_enabled"]:
        return
    for character in settings["hosts"]:
        if character["id"] in _warming:
            continue
        _warming.add(character["id"])
        try:
            await sample(character["id"])
        except Exception as exc:  # noqa: BLE001 – a sample can be recorded later, when someone asks
            log.warning("could not record %s's sample: %s", character["name"], exc)
        finally:
            _warming.discard(character["id"])


def warm_soon() -> None:
    """warm(), without waiting for it."""
    task = asyncio.get_running_loop().create_task(warm())
    _background.add(task)
    task.add_done_callback(_background.discard)

