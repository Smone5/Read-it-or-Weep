"""The host as writer and performer: lines written with delivery tags, performed by ElevenLabs.

ElevenLabs itself is played by a small WebSocket server on this machine, so these tests need neither a
key nor a network, and they check exactly what is sent and what is done with what comes back.
"""
from __future__ import annotations

import asyncio
import base64
import json
from http import HTTPStatus

import numpy as np
import pytest
import websockets

from services import eleven, host_service, settings_store

RATE = host_service.SAMPLE_RATE
LINE = "[delighted] Sam! [warm] Welcome to the show. [building] It's the... [announcing, big] Warm-Up Round!"


def tone(seconds: float, level: float = 0.3) -> bytes:
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * 180 * t) * level * 32767).astype(np.int16).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(RATE * seconds) * 2)


class FakeEleven:
    """Plays ElevenLabs' text-to-dialogue WebSocket: records what it is sent, answers with set audio."""

    def __init__(self, audio: list[bytes], *, refuse: HTTPStatus | None = None, piece: int = 4801):
        self.audio, self.refuse, self.piece = audio, refuse, piece
        self.received: list[dict] = []
        self.paths: list[str] = []
        self.server = None

    async def _refuse(self, connection, request):
        self.paths.append(request.path)
        if self.refuse:
            return connection.respond(self.refuse, json.dumps({"detail": {"message": "invalid key"}}))
        return None

    async def _handle(self, connection):
        try:
            async for raw in connection:
                message = json.loads(raw)
                self.received.append(message)
                if message.get("close_socket"):
                    break
            for chunk in self.audio:                           # odd-sized pieces: samples split across messages
                for start in range(0, len(chunk), self.piece):
                    await connection.send(json.dumps({"audio": base64.b64encode(chunk[start:start + self.piece]).decode()}))
            await connection.send(json.dumps({"audio": None, "is_final": True}))
        except websockets.ConnectionClosed:
            pass

    async def __aenter__(self):
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0, process_request=self._refuse)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/text-to-dialogue/stream-input"
        return self

    async def __aexit__(self, *exc_info):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(eleven, "configured", lambda: True)
    monkeypatch.setattr(eleven, "_headers", lambda: {"xi-api-key": "test-key-not-real"})

    async def same_id(voice):
        return voice.voice_id

    monkeypatch.setattr(eleven, "account_voice_id", same_id)


def host(voice: str = "eleven:classic", *, speak: bool = True) -> host_service.WrittenHostVoice:
    session = host_service.WrittenHostVoice("brief", eleven.BY_ID[voice], speak)

    async def write() -> str:
        return LINE

    session._write = write          # the writer is a text model; here it is a fixed line
    return session


async def collect(stream) -> tuple[list[str], bytes]:
    texts, audio = [], bytearray()
    async for kind, data in stream:
        if kind == "audio":
            audio.extend(data)
        else:
            texts.append(data)
    return texts, bytes(audio)


# --------------------------------------------------------------------------- what the host box shows

def test_the_delivery_tags_are_not_shown():
    assert host_service.spoken_text(LINE) == "Sam! Welcome to the show. It's the... Warm-Up Round!"
    assert host_service.spoken_text("[deadpan] Nope. [amused] ... Bold, though.") == "Nope. ... Bold, though."
    assert host_service.spoken_text("No tags at all.") == "No tags at all."


# --------------------------------------------------------------------------- who performs which voice

def test_an_elevenlabs_voice_without_a_key_is_played_by_its_stand_in(monkeypatch):
    monkeypatch.setattr(eleven, "configured", lambda: False)
    assert host_service.resolve_voice("eleven:british") == {"engine": "realtime", "voice": "coral"}
    assert host_service.resolve_voice("cedar") == {"engine": "realtime", "voice": "cedar"}


def test_an_elevenlabs_voice_with_a_key_is_performed_by_elevenlabs(with_key):
    assert host_service.resolve_voice("eleven:hype") == {"engine": "eleven", "voice": "eleven:hype"}
    assert host_service.resolve_voice("verse") == {"engine": "realtime", "voice": "verse"}
    session = host_service.open_voice("brief", {"engine": "eleven", "voice": "eleven:hype", "manner": "showman"}, True)
    assert isinstance(session, host_service.WrittenHostVoice) and session.voice.voice_id == "DhaeydPYLLVPtHkt04Qp"


def test_every_stand_in_is_a_real_openai_voice():
    for voice in eleven.VOICES:
        assert voice.fallback in settings_store.VOICE_FACTS
        assert voice.id in settings_store.VOICES and settings_store.VOICE_INFO[voice.id]["provider"] == "elevenlabs"


def test_the_default_hosts_are_the_four_elevenlabs_host_voices():
    cleaned = settings_store._clean_host(dict(settings_store.DEFAULTS["host"]))
    assert [h["voice"] for h in cleaned["hosts"]] == [v.id for v in eleven.VOICES]
    assert cleaned["voice"] == "eleven:classic" and cleaned["name"] == "Max"


def test_up_to_four_characters_are_kept():
    hosts = [{"name": f"Host {n}", "voice": "eleven:classic"} for n in range(6)]
    cleaned = settings_store._clean_host({"hosts": hosts})
    assert len(cleaned["hosts"]) == settings_store.MAX_HOSTS == 4


# --------------------------------------------------------------------------- the brief for the writer

def test_a_written_host_directs_the_performance_with_tags():
    tagged = host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=6, tags=True)
    spoken = host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=6)
    assert "How your lines are performed" in tagged and "How you sound" not in tagged
    assert "[announcing" in tagged and "Never a sound effect" in tagged
    assert "How you sound" in spoken and "How your lines are performed" not in spoken
    # Everything else about the character is the same.
    for heading in ("# Who you are", "# The jokes", "# Reacting to an answer", "# Length", "# Variety"):
        assert heading in tagged and heading in spoken


def test_each_manner_has_its_own_colours():
    briefs = {manner: host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=6, manner=manner,
                                                 tags=True) for manner in settings_store.HOST_MANNERS}
    assert len(set(briefs.values())) == len(briefs)
    assert "[deadpan]" in briefs["deadpan"] and "[booming]" in briefs["showman"]


# --------------------------------------------------------------------------- talking to ElevenLabs

async def test_a_line_is_sent_with_its_tags_and_comes_back_as_audio(with_key, monkeypatch):
    async with FakeEleven([tone(0.3), silence(0.9), tone(0.3)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host()
        texts, audio = await collect(session.say("Open the show."))
    assert texts == ["Sam! Welcome to the show. It's the... Warm-Up Round!"]
    assert fake.received[0] == {"voices": ["5XbWatCr4Q4ezek0ZhfT"]}
    assert fake.received[1]["inputs"][0]["text"] == LINE           # the tags go to the performer
    assert fake.received[-1] == {"close_socket": True}
    assert "model_id=eleven_v4_turbo" in fake.paths[0] and "output_format=pcm_24000" in fake.paths[0]
    seconds = len(audio) / 2 / RATE
    assert len(audio) % 2 == 0
    assert 1.05 <= seconds <= 1.25, seconds                         # the 0.9 s pause is cut to half a second
    assert session.history[-1] == {"role": "assistant", "content": LINE}
    assert session.timings[-1]["by"] == "eleven"


async def test_a_wrong_key_hands_the_session_to_the_backup_voice(with_key, monkeypatch):
    async with FakeEleven([tone(0.3)], refuse=HTTPStatus.UNAUTHORIZED) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host()
        spoken_by_backup: list[str] = []

        async def backup(words: str):
            spoken_by_backup.append(words)
            yield tone(0.4)

        session._backup = backup
        texts, audio = await collect(session.say("Open the show."))
        assert spoken_by_backup == ["Sam! Welcome to the show. It's the... Warm-Up Round!"]    # never the tags
        assert session.backup_only                                  # a refusal is not tried again
        await collect(session.say("Another line."))
    assert len(fake.paths) == 1 and len(spoken_by_backup) == 2
    assert 0.35 <= len(audio) / 2 / RATE <= 0.5


async def test_one_failure_is_tried_again_two_in_a_row_are_not(with_key, monkeypatch):
    monkeypatch.setattr(eleven, "SOCKET", "ws://127.0.0.1:9/nothing-listens-here")
    session = host()
    calls = []

    async def backup(words: str):
        calls.append(words)
        yield tone(0.2)

    session._backup = backup
    await collect(session.say("One."))
    assert session.failures == 1 and not session.backup_only
    await collect(session.say("Two."))
    assert session.backup_only and len(calls) == 2


async def test_cutting_the_host_off_stops_the_audio(with_key, monkeypatch):
    async with FakeEleven([tone(2.0)], piece=2400) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host()
        heard = bytearray()
        async for kind, data in session.say("Open the show."):
            if kind == "audio":
                heard.extend(data)
                if len(heard) > RATE * 2 * 0.3:
                    await session.interrupt()
        assert len(heard) / 2 / RATE < 0.6
        assert not session.stopped                                  # ready for the next line
        _, again = await collect(session.say("Next."))
    assert len(again) / 2 / RATE > 1.5


async def test_with_the_sound_off_the_host_only_writes(with_key, monkeypatch):
    monkeypatch.setattr(eleven, "SOCKET", "ws://127.0.0.1:9/never-called")
    session = host(speak=False)
    texts, audio = await collect(session.say("Open the show."))
    assert texts and audio == b""


async def test_what_the_contestant_says_is_written_down_and_answered(with_key, monkeypatch):
    async with FakeEleven([tone(0.3)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host()

        async def transcribe(pcm: bytes) -> str:
            assert len(pcm) == len(tone(0.5))
            return "Can you read the question again?"

        monkeypatch.setattr(host_service, "transcribe", transcribe)
        await session.hear(tone(0.5))
        texts, audio = await collect(session.reply(f"(Stage note: Sam just spoke to you; {host_service.WORDS_IN_AUDIO}.)"))
    note = session.history[-2]["content"]
    assert "Can you read the question again?" in note and host_service.WORDS_IN_AUDIO not in note
    assert texts and audio and session.heard == bytearray()


async def test_nothing_heard_means_nothing_said(with_key, monkeypatch):
    async def transcribe(pcm: bytes) -> str:
        return ""

    monkeypatch.setattr(host_service, "transcribe", transcribe)
    session = host()
    await session.hear(tone(0.5))
    texts, audio = await collect(session.reply("(Stage note.)"))
    assert texts == [] and audio == b"" and session.history == []


# --------------------------------------------------------------------------- finding the voices in the account

def test_a_voice_saved_from_its_share_link_is_found_by_its_original_id():
    items = [
        {"voice_id": "premade123", "name": "Roger - Laid-Back", "sharing": None},
        {"voice_id": "accountCopyOfClassic", "name": "Classic host (renamed)",
         "sharing": {"original_voice_id": "5XbWatCr4Q4ezek0ZhfT"}},
        {"voice_id": "whydKScabPmVENfyGF8q", "name": "Anything", "sharing": {}},
        {"voice_id": "byNameOnly", "name": "The Southern Charmer", "sharing": {}},
    ]
    found = eleven.match_account_voices(items)
    assert found == {"eleven:classic": "accountCopyOfClassic", "eleven:british": "whydKScabPmVENfyGF8q",
                     "eleven:southern": "byNameOnly"}                    # the hype master is not in this account


def test_each_voice_has_a_link_to_add_it():
    for voice in eleven.VOICES:
        assert voice.share_link.endswith(f"/{voice.owner_id}/{voice.voice_id}")
        assert voice.share_link.startswith("https://elevenlabs.io/app/voice-lab/share/")


async def test_a_missing_voice_hands_the_session_to_the_backup_at_once(with_key, monkeypatch):
    class Missing(FakeEleven):
        async def _handle(self, connection):
            async for raw in connection:
                self.received.append(json.loads(raw))
                if self.received[-1].get("close_socket"):
                    break
            await connection.send(json.dumps({"message": "voice_not_found", "error": "voice_not_found"}))

    async with Missing([]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host()

        async def backup(words: str):
            yield tone(0.3)

        session._backup = backup
        await collect(session.say("Open the show."))
    assert session.backup_only


# --------------------------------------------------------------------------- the writer's reply, said once

class _Part:
    def __init__(self, text):
        self.text = text


class _Message:
    type = "message"

    def __init__(self, text, phase=None):
        self.phase, self.content = phase, [_Part(text)]


class _Reply:
    def __init__(self, *messages):
        self.output = list(messages)
        self.output_text = "".join(m.content[0].text for m in messages)


LINE_BACK = "[grinning] Aaron, welcome back! [building] It's the... [announcing, big] Warm-Up!"


def test_only_the_final_answer_is_used_never_the_draft_before_it():
    import llm
    # Both shapes seen from the writer on 2026-11-20, which made the host welcome a player twice in a row.
    assert llm.final_text(_Reply(_Message(LINE_BACK, "commentary"), _Message(LINE_BACK, "final_answer"))) == LINE_BACK
    assert llm.final_text(_Reply(_Message(LINE_BACK, "final_answer"), _Message(LINE_BACK, "final_answer"))) == LINE_BACK
    assert llm.final_text(_Reply(_Message("Let me count the words.", "commentary"), _Message(LINE_BACK, "final_answer"))) == LINE_BACK
    assert llm.final_text(_Reply(_Message(LINE_BACK))) == LINE_BACK                        # a model without phases


def test_a_line_said_twice_or_followed_by_notes_is_tidied():
    tidy = host_service.tidy_line
    assert tidy(f"{LINE_BACK} {LINE_BACK}") == LINE_BACK
    assert tidy("Aaron, welcome back. First up... Warm-Up! Aaron, welcome back. First up... Warm-Up!") == \
        "Aaron, welcome back. First up... Warm-Up!"
    assert tidy("Aaron, welcome back. Let's begin... Warm-Up! 14 words. Count: Aaron(1) welcome2 back3") == \
        "Aaron, welcome back. Let's begin... Warm-Up!"
    assert tidy("Aaron, welcome back! It's the... Warm-Up! </final> } Aaron, welcome back!") == \
        "Aaron, welcome back! It's the... Warm-Up!"
    assert tidy("Aaron, welcome back! It’s the... Warm-Up! 亚洲AVassistant numerusform") == "Aaron, welcome back! It’s the... Warm-Up!"
    # An ordinary line is left exactly as it is - including one that repeats a word or a phrase on purpose.
    for line in (LINE_BACK, "[impressed] Yes! And fast. [suspicious] ... Suspiciously fast.",
                 "[deadpan] No. No, no, no. [plainly] It was Phoenix.", "\"Nope.\""):
        assert tidy(line) == line.strip('"')


# --------------------------------------------------------------------------- the show's opening

def test_the_opening_explains_this_game_s_own_rules():
    rules = settings_store.DEFAULTS["game"]
    show = {"mode": "show", "lifelines": {"fifty": 1, "peek": 1, "hint": 0}}
    rounds = ["Warm-Up", "Music Break", "Picture Round", "The Deep End", "Lightning Round", "Second Chance", "Final Wager"]
    told = host_service._how_it_works(show, rules, rounds)
    assert "7 rounds" in told and "Warm-Up, Music Break" in told and "30 seconds" in told
    assert "name that tune" in told and "Fact or Fake" in told and "bet their points" in told
    assert "50/50" in told and "peek" in told and "hint" not in told.split("Lifelines")[1]   # a used-up lifeline is not offered
    assert "3 wrong answers" in host_service._how_it_works({"mode": "sudden_death", "lifelines": {}}, rules, ["Level 1"])
    assert "90 seconds" in host_service._how_it_works({"mode": "speed", "lifelines": {}}, rules, ["Speed Run"])
    assert "80% to pass" in host_service._how_it_works({"mode": "straight", "lifelines": {}}, rules, ["The Quiz"])


async def test_an_opening_far_over_its_word_limit_is_rewritten_once(monkeypatch):
    calls = []

    async def write_line(instructions, history):
        calls.append(history[-1]["content"])
        return "[warm] " + " ".join(["word"] * (120 if len(calls) == 1 else 60)) + " Warm-Up!"

    monkeypatch.setattr(host_service, "write_line", write_line)
    session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], False)
    await session.note("Open the show.\n\nWord limit: 65.")
    line = await session._write()
    assert len(calls) == 2 and "the limit is 65" in calls[1]
    assert len(host_service.spoken_text(line).split()) == 61
    # Near the limit (up to 15 words over): written once - a rewrite doubles the wait before the host speaks.
    calls.clear()
    monkeypatch.setattr(host_service, "write_line", lambda i, h: _line_of(78))
    await session.note("Open the show.\n\nWord limit: 65.")
    assert len(host_service.spoken_text(await session._write()).split()) == 78


async def _line_of(words: int) -> str:
    return " ".join(["word"] * words)


async def test_a_slow_draft_is_used_as_it_is_rather_than_rewritten(monkeypatch):
    # A rewrite doubles the wait; after a slow first draft it could make the host miss his moment entirely.
    ticks = iter([100.0, 105.0])
    monkeypatch.setattr(host_service, "_clock", lambda: next(ticks))
    calls = []

    async def write_line(instructions, history):
        calls.append(history[-1]["content"])
        return " ".join(["word"] * 120)

    monkeypatch.setattr(host_service, "write_line", write_line)
    session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], False)
    await session.note("Open the show.\n\nWord limit: 65.")
    assert len(host_service.spoken_text(await session._write()).split()) == 120 and len(calls) == 1


async def test_a_line_cut_off_while_it_is_being_written_is_dropped_at_once(monkeypatch):
    # The player tapped Next while his reaction was still being written: the next line must not wait for it.
    async def write_line(instructions, history):
        await asyncio.sleep(5)
        return "[dry] Too late."

    monkeypatch.setattr(host_service, "write_line", write_line)
    session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], False)

    async def listen():
        return [piece async for piece in session.say("React.")]

    task = asyncio.ensure_future(listen())
    await asyncio.sleep(0.1)
    await session.interrupt()
    assert await asyncio.wait_for(task, 0.5) == []
    assert session.stopped is False                      # ready for the next line


# --------------------------------------------------------------------------- what he is asked to say, and how long

class _Db:
    """Answers the few lookups the host's stage directions make."""

    def __init__(self, item=None, track=None, best=None, tally=None):
        self.item, self.track, self.best, self.tally = item, track, best, tally

    async def fetchrow(self, query, *args):
        if "from run_items where id" in query:
            return self.item
        if "from music_tracks" in query:
            return self.track
        if "count(*)" in query:
            return self.tally
        return None

    async def fetchval(self, query, *args):
        return self.best if "max(score)" in query else None


RUN = {"id": "run-1", "quiz_id": "quiz-1", "player_id": "player-1", "mode": "show", "decade": "1980s", "score": 120,
       "status": "completed", "report_correct": 6, "report_answered": 10, "streak": 0, "streak_max": 3,
       "badge": "Speed Reader", "questions_answered": 5, "plan": []}
PLAYER = {"name": "Aaron Smith", "team": "Sales", "games_played_count": 2}


def _item(**changes):
    item = {"id": "item-1", "slot": "report", "round_name": "Warm-Up", "round_no": 1, "answered_at": "now",
            "payload": {"text": "Which hub moves the most pallets?", "options": ["Reno", "Phoenix", "Dallas", "Tulsa"]},
            "correct_index": 1, "selected_index": 0, "is_correct": False, "timed_out": False, "time_limit_sec": 30,
            "time_taken_ms": 9000, "lifelines_used": [], "wager": None, "music_track_id": None}
    item.update(changes)
    return item


async def _direction(monkeypatch, cue, item=None, run=None, **db):
    monkeypatch.setattr(host_service, "db", _Db(item=item, **db))
    return await host_service.describe_cue(cue, run=run or RUN, player=PLAYER, item_id="item-1" if item else None)


def _limit(direction: str) -> int:
    import re
    found = re.search(r"Word limit: (\d+)\.", direction)       # the form WrittenHostVoice._write enforces
    assert found, direction
    return int(found.group(1))


async def test_a_reaction_ends_on_its_joke_with_a_limit_for_each_case(monkeypatch):
    wrong = await _direction(monkeypatch, "result", _item())
    assert _limit(wrong) == 14 and "right answer inside it" in wrong and "ending on its funniest word" in wrong
    right = await _direction(monkeypatch, "result", _item(selected_index=1, is_correct=True))
    assert _limit(right) == 10 and "ending on its funniest word" in right
    late = await _direction(monkeypatch, "result", _item(selected_index=None, timed_out=True))
    assert _limit(late) == 14 and "right answer inside it" in late
    long_answer = _item(payload={"text": "Why?", "options": ["Reno lacks spare peak-season capacity", "b", "c", "d"]},
                        correct_index=0, selected_index=1)
    assert "gist, in four words or fewer" in await _direction(monkeypatch, "result", long_answer)
    assert "gist" not in wrong                                                 # "Phoenix" is short enough to say


async def test_a_song_reaction_ends_on_the_title_like_a_dj(monkeypatch):
    track = {"title": "Heart of Glass", "artist": "Blondie"}
    for changes in ({}, {"selected_index": 1, "is_correct": True}, {"selected_index": None, "timed_out": True}):
        direction = await _direction(monkeypatch, "result", _item(slot="music", music_track_id="t-1", **changes),
                                     track=track)
        assert _limit(direction) == 16 and "title and artist, like a DJ" in direction and "Heart of Glass" in direction


async def test_a_round_is_announced_short_and_a_music_break_ends_on_the_throw(monkeypatch):
    plain = await _direction(monkeypatch, "round", _item(round_name="The Deep End", round_no=4, answered_at=None))
    assert _limit(plain) == 12 and "\"The Deep End!\"" in plain and "six words or fewer" in plain
    music = await _direction(monkeypatch, "round", _item(slot="music", round_name="Music Break", answered_at=None))
    assert _limit(music) == 12 and "Roll it!" in music and "plays the moment you stop" in music
    bonus = await _direction(monkeypatch, "round", _item(slot="music", round_name="Music Break", answered_at=None),
                             run={**RUN, "mode": "sudden_death"})
    assert _limit(bonus) == 14 and "Roll it!" in bonus and "song or the artist" in bonus
    wager = await _direction(monkeypatch, "round", _item(round_name="Final Wager", round_no=7, answered_at=None))
    assert _limit(wager) == 14 and "Place your bet!" in wager
    after_lightning = await _direction(monkeypatch, "round", _item(round_name="Second Chance", round_no=6,
                                                                   answered_at=None), tally={"asked": 6, "right_ones": 2})
    assert _limit(after_lightning) == 18 and "2 out of 6" in after_lightning


async def test_the_jargon_round_throws_to_its_song_and_back_announces_it_over_the_top(monkeypatch):
    # The song now plays under the jargon title (user, 2026-11-20: the background music was playing instead of it).
    jukebox_run = {**RUN, "mode": "jukebox"}
    intro = await _direction(monkeypatch, "round", _item(slot="jargon", round_name="Jargon Jukebox", round_no=2,
                                                         answered_at=None), run=jukebox_run)
    assert "\"Jargon Jukebox!\"" in intro and "Roll it!" in intro and "plays the moment you stop" in intro
    riddle = _item(slot="jargon", round_name="Jargon Jukebox", music_track_id="t-1",
                   payload={"text": "Cardiac Glass Ceiling", "options": ["Heart of Glass", "b", "c", "d"]}, correct_index=0)
    reaction = await _direction(monkeypatch, "result", riddle, run=jukebox_run,
                                track={"title": "Heart of Glass", "artist": "Blondie"})
    assert _limit(reaction) == 16 and "Heart of Glass" in reaction and "it is playing under you" in reaction
    assert "plays the moment you stop" not in reaction


async def test_the_sign_off_ends_on_the_show_s_name_and_knows_what_the_screen_says(monkeypatch):
    first_game = await _direction(monkeypatch, "summary", best=None)
    assert _limit(first_game) == 24 and "show's name as your very last words" in first_game
    assert "'Speed Reader'" in first_game and "New high score!" in first_game and "their first game" in first_game
    beaten = await _direction(monkeypatch, "summary", best=90)
    assert "New high score!" in beaten and "first game" not in beaten
    not_beaten = await _direction(monkeypatch, "summary", best=300)
    assert "New high score!" not in not_beaten


def test_the_persona_makes_the_last_word_the_button():
    persona = host_service.build_persona(name="Max", game_title="Read It or Weep", company="Acme", sarcasm=6, tags=True)
    assert "your LAST word is the button" in persona and "INSIDE the verdict" in persona
    assert "10 words when they are right, 14 when they are wrong or out of time, 16 for a song" in persona


def test_the_host_stays_safe_for_work():
    # "Me and the report are meeting again... someone should book a room" - wrong for a work quiz (2026-11-20).
    for tags in (True, False):
        persona = host_service.build_persona(name="Max", game_title="Read It or Weep", company="Acme", sarcasm=8, tags=tags)
        assert "no romance, dating, flirting or innuendo" in persona and "get a room" in persona
        assert "Never assume anyone's gender from a name" in persona
    angles = host_service._opening_angles({"decade": "80s"}, {"team": "Sales"},
                                          {"title": "Q3 Review", "meeting_at": None}, returning=True)
    assert any("sports-style" in angle for angle in angles)
    assert not any(word in angle for angle in angles for word in ("meet again", "date", "relationship", "romance"))



# --------------------------------------------------------------------------- reading out the final question

def test_the_final_question_s_clock_waits_for_its_reading():
    from services import engine
    assert engine.reading_seconds("Which hub moved the most pallets in the third quarter of the year?") == (3 + 13) / 2 + 1.5
    assert engine.reading_seconds(" ".join(["word"] * 80)) == engine.READ_MAX_SEC        # never a long wait
    assert engine.reading_seconds(None) == 3 / 2 + 1.5


async def test_only_the_final_question_is_read_and_word_for_word(monkeypatch):
    question = "Which hub moved the most pallets in Q3?"
    final = {"slot": "final", "wager": 100, "answered_at": None, "payload": {"text": question}}
    monkeypatch.setattr(host_service, "db", _Db(item=final))
    line = await host_service.final_question_line(RUN, "item-1")
    assert line == f"[dramatic] Your final question... [clearly] {question}"
    assert host_service.spoken_text(line) == f"Your final question... {question}"
    for other in ({**final, "slot": "report"}, {**final, "wager": None}, {**final, "answered_at": "now"}):
        monkeypatch.setattr(host_service, "db", _Db(item=other))
        assert await host_service.final_question_line(RUN, "item-1") is None
    assert await host_service.final_question_line(RUN, None) is None


async def test_a_reading_is_performed_as_written_without_the_writer(with_key, monkeypatch):
    async def no_writer(*args):
        raise AssertionError("a reading must not go through the writer")

    monkeypatch.setattr(host_service, "write_line", no_writer)
    line = "[dramatic] Your final question... [clearly] Which hub moved the most pallets in Q3?"
    async with FakeEleven([tone(0.4)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)
        texts, audio = await collect(session.recite(line))
    assert texts == ["Your final question... Which hub moved the most pallets in Q3?"]
    assert fake.received[1]["inputs"][0]["text"] == line
    assert len(audio) > 0 and session.stopped is False



# --------------------------------------------------------------------------- said once, replayed after

@pytest.fixture
def recordings(monkeypatch):
    """The recorded-lines store and the round sets, kept in memory."""
    store: dict[str, bytes] = {}
    banks: dict[str, list[dict]] = {}

    async def recorded(key):
        return store.get(key)

    async def record(key, voice, line, audio):
        store[key] = audio

    async def bank_lines(bank):
        return [dict(row) for row in banks.get(bank, [])]

    async def bank_add(bank, line):
        banks.setdefault(bank, []).append({"id": len(banks.get(bank, [])) + 1, "line": line, "used_count": 1})

    async def bank_used(line_id):
        for rows in banks.values():
            for row in rows:
                if row["id"] == line_id:
                    row["used_count"] += 1

    monkeypatch.setattr(host_service, "_recorded", recorded)
    monkeypatch.setattr(host_service, "_record", record)
    monkeypatch.setattr(host_service, "_bank_lines", bank_lines)
    monkeypatch.setattr(host_service, "_bank_add", bank_add)
    monkeypatch.setattr(host_service, "_bank_used", bank_used)
    return store, banks


async def test_a_kept_line_is_recorded_once_and_replayed_after(with_key, monkeypatch, recordings):
    store, _ = recordings
    line = "[building] Next up... [announcing] the Warm-Up!"
    async with FakeEleven([tone(0.5)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        first = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)
        _, heard = await collect(first.recite(line, note="Announce the round."))
        asked = len(fake.paths)
        again = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)
        _, replayed = await collect(again.recite(line, note="Announce the round."))
    assert asked == 1 and len(fake.paths) == 1                  # the second time ElevenLabs is not asked at all
    assert replayed == heard and len(store) == 1 and again.last_by == "replay"


async def test_a_line_cut_short_or_in_the_backup_voice_is_not_kept(with_key, monkeypatch, recordings):
    store, _ = recordings
    async with FakeEleven([tone(0.4)], refuse=HTTPStatus.UNAUTHORIZED) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)

        async def backup(words):
            yield tone(0.3)

        monkeypatch.setattr(session, "_backup", backup)
        await collect(session.recite("[dry] Roll it!", note="Throw to the song."))
    assert store == {} and session.last_by == "backup"


async def test_round_introductions_rotate_once_their_set_is_full(monkeypatch, recordings):
    _, banks = recordings
    written = []

    async def write_line(instructions, history):
        written.append(history)
        return f"[announcing] Line {len(written)}!"

    monkeypatch.setattr(host_service, "write_line", write_line)
    session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)
    lines = [await session.from_set("show|report|Warm-Up|", "Announce the next round.") for _ in range(20)]
    assert len(written) == host_service.ROUND_SET                 # written (and recorded) only until the set is full
    assert all(len(history) == 1 for history in written)          # without the game so far: they fit any game
    assert set(lines) == {f"[announcing] Line {n}!" for n in range(1, host_service.ROUND_SET + 1)}
    # A different host (another brief) or another round starts a set of its own.
    other = host_service.WrittenHostVoice("another brief", eleven.BY_ID["eleven:classic"], True)
    await other.from_set("show|report|Warm-Up|", "Announce the next round.")
    assert len(banks) == 2


async def test_past_the_budget_new_lines_use_the_backup_voice(with_key, monkeypatch):
    async def share_used():
        return 0.93

    monkeypatch.setattr(eleven, "share_used", share_used)
    async with FakeEleven([tone(0.3)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True, budget=90)

        async def backup(words):
            yield tone(0.3)

        monkeypatch.setattr(session, "_backup", backup)
        _, audio = await collect(session.recite("[dry] Roll it!", note="Throw.", keep=False))
    assert fake.paths == [] and session.last_by == "backup" and len(audio) > 0
    unguarded = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True, budget=0)
    assert unguarded.budget == 0


async def test_a_busy_elevenlabs_is_tried_again_not_given_up_on(with_key, monkeypatch):
    class BusyOnce(FakeEleven):
        tries = 0

        async def _refuse(self, connection, request):
            self.paths.append(request.path)
            BusyOnce.tries += 1
            if BusyOnce.tries == 1:
                return connection.respond(HTTPStatus.TOO_MANY_REQUESTS, json.dumps({"detail": {"message": "too many concurrent requests"}}))
            return None

    monkeypatch.setattr(eleven, "_slots", None)
    async with BusyOnce([tone(0.3)]) as fake:
        monkeypatch.setattr(eleven, "SOCKET", fake.url)
        session = host_service.WrittenHostVoice("brief", eleven.BY_ID["eleven:classic"], True)
        _, audio = await collect(session.recite("[dry] Roll it!", note="Throw.", keep=False))
    assert len(fake.paths) == 2 and len(audio) > 0 and session.last_by == "eleven" and not session.backup_only


def test_the_budget_setting_stays_within_its_choices():
    cleaned = settings_store._clean_host({"voice_budget": 85})
    assert cleaned["voice_budget"] == settings_store.DEFAULTS["host"]["voice_budget"] == 90
    assert settings_store._clean_host({"voice_budget": "75"})["voice_budget"] == 75
    assert settings_store._clean_host({"voice_budget": 0})["voice_budget"] == 0
