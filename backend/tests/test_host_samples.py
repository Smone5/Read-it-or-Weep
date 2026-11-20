"""The hosts' introductions in the lobby: what makes a new one, and how each kind of voice is recorded."""
from __future__ import annotations

import copy

from services import eleven, host_samples, host_service, settings_store

SETTINGS = settings_store._clean_host(copy.deepcopy(settings_store.DEFAULTS["host"]))
BRANDING = {"game_title": "Read It or Weep", "company_name": "Meridian"}
MAX = SETTINGS["hosts"][0]


def test_a_sample_is_kept_until_something_about_the_host_changes(monkeypatch):
    monkeypatch.setattr(eleven, "configured", lambda: True)
    key, performer = host_samples.sample_key(MAX, SETTINGS, BRANDING)
    assert performer == {"engine": "eleven", "voice": "eleven:classic"}
    assert host_samples.sample_key(dict(MAX), dict(SETTINGS), dict(BRANDING))[0] == key        # same host: same sample
    for changed in ({**MAX, "name": "Maxine"}, {**MAX, "voice": "eleven:hype"}, {**MAX, "style": "Grumpy."},
                    {**MAX, "manner": "deadpan"}):
        assert host_samples.sample_key(changed, SETTINGS, BRANDING)[0] != key
    assert host_samples.sample_key(MAX, {**SETTINGS, "sarcasm": 1}, BRANDING)[0] != key
    assert host_samples.sample_key(MAX, SETTINGS, {**BRANDING, "game_title": "Other"})[0] != key
    monkeypatch.setattr(eleven, "configured", lambda: False)                                   # a key taken away
    other, performer = host_samples.sample_key(MAX, SETTINGS, BRANDING)
    assert other != key and performer == {"engine": "realtime", "voice": "cedar"}


async def test_an_elevenlabs_host_is_recorded_as_mp3_with_its_tags(monkeypatch):
    sent = {}

    async def write_line(instructions, history):
        assert "How your lines are performed" in instructions and "Max" in instructions
        assert "Introduce yourself" in history[0]["content"]
        return "[grinning] I'm Max. [teasing] Pick me... [dry] I'm cheaper."

    async def perform(text, voice, *, output_format):
        sent.update(text=text, voice=voice.id, format=output_format)
        yield b"ID3" + bytes(3000)

    monkeypatch.setattr(host_service, "write_line", write_line)
    monkeypatch.setattr(eleven, "perform", perform)
    mime, audio, words = await host_samples._record(MAX, SETTINGS, BRANDING, {"engine": "eleven", "voice": "eleven:classic"})
    assert mime == "audio/mpeg" and audio.startswith(b"ID3")
    assert words == "I'm Max. Pick me... I'm cheaper."
    assert sent == {"text": "[grinning] I'm Max. [teasing] Pick me... [dry] I'm cheaper.", "voice": "eleven:classic",
                    "format": "mp3_44100_64"}


async def test_if_elevenlabs_fails_the_backup_voice_records_it(monkeypatch):
    async def write_line(instructions, history):
        return "[grinning] I'm Max."

    async def perform(text, voice, *, output_format):
        raise eleven.ElevenError("down")
        yield b""  # pragma: no cover

    async def backup(words, fallback, manner):
        assert (words, fallback) == ("I'm Max.", "cedar")
        return b"ID3backup" + bytes(3000)

    monkeypatch.setattr(host_service, "write_line", write_line)
    monkeypatch.setattr(eleven, "perform", perform)
    monkeypatch.setattr(host_samples, "_backup_mp3", backup)
    mime, audio, words = await host_samples._record(MAX, SETTINGS, BRANDING, {"engine": "eleven", "voice": "eleven:classic"})
    assert mime == "audio/mpeg" and audio.startswith(b"ID3backup") and words == "I'm Max."


async def test_an_openai_voice_is_recorded_as_wav(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def say(self, direction):
            assert "Introduce yourself" in direction
            yield "text", "I'm Max, "
            yield "audio", bytes(24000 * 2)
            yield "text", "pick me."

    seen = {}

    def open_voice(persona, host, speak):
        seen.update(engine=host["engine"], voice=host["voice"], tags="How your lines are performed" in persona)
        return Session()

    monkeypatch.setattr(host_service, "open_voice", open_voice)
    mime, audio, words = await host_samples._record(MAX, SETTINGS, BRANDING, {"engine": "realtime", "voice": "cedar"})
    assert mime == "audio/wav" and audio[:4] == b"RIFF" and len(audio) > 48000
    assert words == "I'm Max, pick me." and seen == {"engine": "realtime", "voice": "cedar", "tags": False}
