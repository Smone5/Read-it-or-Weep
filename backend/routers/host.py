"""WebSocket for the talking host (solo games and the live big screen).

Every line the browser asks for carries an ``id`` – a counter the browser keeps – so both sides
can talk about one particular line: whether it started, when it ended, and that it is no longer
wanted. That is what keeps the host from talking over the next question.

Browser → server
    {"type": "hello", "run": "...", "token": "...", ...}          first message: who is asking, and for which game.
                                                                  (Sent as a message, never in the address, so that
                                                                  sign-in tokens do not end up in server logs.)
    {"type": "cue", "id": 7, "cue": "result", "itemId": "..."}   react to something that happened
    {"type": "cancel", "through": 7}                              drop every line up to and including 7,
                                                                  cutting it off if it is being said now
    {"type": "cancel", "only": 7}                                 drop just that one
    {"type": "talk.start"}                                        the contestant pressed the talk button
    <binary frames>                                               their microphone, 24 kHz 16-bit mono
    {"type": "talk.end", "id": 8} / {"type": "talk.cancel"}       they let go

Server → browser – each requested line gets exactly one ending, ``line.end`` or ``line.skip``
    {"type": "ready", ...}
    {"type": "line.start", "id": 7, "cue": "..."}, {"type": "line.text", "id": 7, "delta": "..."}, <binary audio>,
    {"type": "line.end", "id": 7, "cut": false}
    {"type": "line.skip", "id": 7}                                nothing will be said for this one
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import db
from security import TokenError, read_player_token
from services import host_service, live

log = logging.getLogger("host.ws")
router = APIRouter()

MAX_TALK_BYTES = 20 * host_service.SAMPLE_RATE * 2         # 20 seconds of speech
MIN_TALK_BYTES = int(0.35 * host_service.SAMPLE_RATE * 2)  # ignore accidental taps
HELLO_TIMEOUT_SEC = 8

# Every line costs money, so one game cannot open voice channels (or ask for lines) without end.
MAX_SOCKETS_PER_GAME = 2          # the game itself, plus one more while it reconnects
MAX_LINES_PER_GAME = 220          # across all of that game's connections
_open: dict[str, int] = {}
_said: dict[str, int] = {}


def _count_line(game: str) -> None:
    _said[game] = _said.get(game, 0) + 1
    while len(_said) > 5000:                                # forget the oldest games
        _said.pop(next(iter(_said)))


async def _live_question_line(session: dict) -> str | None:
    """Fast verbatim read for non-music live questions (skips LLM latency and avoids paraphrasing)."""
    index, items = session["current_index"], session["items"]
    item = items[index] if 0 <= index < len(items) else None
    if not item or session["status"] != "question" or item["kind"] == "music":
        return None
    if live.is_double_points(index, len(items)):
        lead = f"Question {index + 1}, double points!"
    elif index == 0:
        lead = "First question!"
    else:
        lead = f"Question {index + 1}."
    return f"{lead} {item['text']}"


async def _live_direction(session: dict, cue: str) -> str | None:
    """Stage directions for the big-screen host, built from the live session's own data."""
    index, items = session["current_index"], session["items"]
    item = items[index] if 0 <= index < len(items) else None
    if cue == "live_welcome":
        players = await db.fetchval("select count(*) from live_participants where session_id = $1", session["id"])
        return (f"Open the live show. {players} players are in the room with their phones out. Welcome the room in "
                "two short sentences and tell them to keep their phones ready. Word limit: 25.")
    if cue == "live_question" and item and session["status"] == "question":
        double = " This is a DOUBLE POINTS question!" if live.is_double_points(index, len(items)) else ""
        if item["kind"] == "music":
            return (f"Announce a music break.{double} Its name first, as a headline - \"Music Break!\" - then one quick "
                    "quip. Do not name the song or artist. The clip starts as you finish. Word limit: 15.")
        return (f"Read question {index + 1} to the room, clearly and at an easy pace, exactly as written, after a lead-in "
                f"of at most four words{(' (mention DOUBLE POINTS in the lead-in)' if double else '')}. Do not read the options "
                f"and do not add a comment afterwards. The question: \"{item['text']}\"")
    if cue == "live_reveal" and item and session["status"] == "reveal":
        totals = await db.fetchrow(
            "select count(*) as answered, count(*) filter (where is_correct) as correct from live_answers"
            " where session_id = $1 and question_index = $2", session["id"], index)
        fastest = await db.fetchrow(
            "select lp.display_name, la.time_ms from live_answers la"
            " join live_participants lp on lp.id = la.participant_id"
            " where la.session_id = $1 and la.question_index = $2 and la.is_correct"
            " order by la.time_ms asc limit 1", session["id"], index)
        percent = round(100 * totals["correct"] / totals["answered"]) if totals["answered"] else 0
        answer = item["options"][item["correct_index"]]
        if item["kind"] == "music":
            answer = f"\"{item['music']['title']}\" by {item['music']['artist']}"
        fast_note = (f" Fastest finger: {fastest['display_name']} in {fastest['time_ms'] / 1000:.1f} seconds."
                     if fastest else "")
        return (f"Reveal the answer to the room: {answer}. {percent}% of the room got it right.{fast_note} Start with the answer "
                "itself - no verdict call such as \"Correct!\", because the room gave many answers - then ONE quick line "
                "about how the room did. Word limit: 18.")
    if cue in ("live_scoreboard", "live_final") and session["status"] in ("scoreboard", "finished"):
        snap = await live.state(session, host=True, player=None)
        standings = snap.get("standings") or []
        if not standings:
            return None
        names = ", ".join(f"{t['name']} on {t['score']}" for t in standings[:3])
        mover = snap.get("biggestMover")
        teams = snap.get("teams") or []
        mover_note = (f" Biggest mover: {mover['name']} jumped {mover['delta']} spots to #{mover['rank']}."
                      if mover else "")
        team_note = (f" Leading team: {teams[0]['team']} (avg {teams[0]['average']})."
                     if len(teams) >= 2 else "")
        if cue == "live_final":
            return (f"The live game is over. Final top three: {names}.{team_note} Crown the winner and sign off in two short "
                    "sentences. Word limit: 25.")
        return (f"Scoreboard check. Current top three: {names}.{mover_note}{team_note} "
                "One quick line about the race. Word limit: 18.")
    return None


@router.websocket("/api/host/ws")
async def host_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        hello = json.loads(await asyncio.wait_for(websocket.receive_text(), HELLO_TIMEOUT_SEC))
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            raise ValueError("no hello")
    except Exception:  # noqa: BLE001 – anything but a proper hello is refused
        await websocket.close(code=4401)
        return
    token, run = str(hello.get("token") or ""), str(hello.get("run") or "")
    live_code, key = str(hello.get("liveCode") or ""), str(hello.get("key") or "")
    host_id, voice = str(hello.get("hostId") or ""), 1 if hello.get("voice", True) else 0

    player = run_row = quiz = session = None
    try:
        if live_code:
            session = await live.load(live_code, fresh=True)
            if not live.is_host(session, key):
                raise PermissionError
            quiz = await db.fetchrow("select * from quizzes where id = $1", session["quiz_id"])
        else:
            player = await db.fetchrow("select * from players where id = $1", read_player_token(token))
            run_row = await db.fetchrow("select * from runs where id = $1", run)
            if player is None or run_row is None or run_row["player_id"] != player["id"]:
                raise PermissionError
            quiz = await db.fetchrow("select * from quizzes where id = $1", run_row["quiz_id"])
    except (TokenError, PermissionError, live.LiveError, ValueError):
        await websocket.close(code=4401)
        return
    except Exception:  # noqa: BLE001 – e.g. an id that is not an id
        await websocket.close(code=4401)
        return

    game = f"live:{session['id']}" if session is not None else f"run:{run_row['id']}"
    if _open.get(game, 0) >= MAX_SOCKETS_PER_GAME:
        await websocket.close(code=4429)
        return
    _open[game] = _open.get(game, 0) + 1
    try:
        await _serve(websocket, game=game, quiz=quiz, player=player, run_row=run_row, session=session,
                     live_code=live_code, live_key=key, host_id=host_id, voice=voice)
    finally:
        if _open.get(game, 0) <= 1:
            _open.pop(game, None)
        else:
            _open[game] -= 1


async def _serve(websocket: WebSocket, *, game: str, quiz: dict, player: dict | None, run_row: dict | None,
                 session: dict | None, live_code: str, live_key: str, host_id: str, voice: int) -> None:
    """One voice channel, from the model's first breath to the socket closing."""

    instructions, host = await host_service.persona(quiz, straight=bool(run_row and run_row["mode"] == "straight"),
                                                    host_id=host_id or None, room=session is not None)
    speak = bool(host["voice_enabled"]) and voice == 1
    can_talk = speak and bool(host.get("talk_enabled", True)) and run_row is not None

    actions: asyncio.Queue = asyncio.Queue()
    talk = {"open": False, "bytes": 0}
    # Who has the floor. "void" is the highest line id the browser no longer wants; "seen" the highest it has
    # asked for; "speaking" the id of the line going out right now (0 = silence); "cut" whether it was cut off.
    floor = {"void": 0, "seen": 0, "speaking": 0, "cut": False}
    gone: set[int] = set()                                   # single lines the browser gave up waiting for

    def unwanted(number: int) -> bool:
        return bool(number) and (number <= floor["void"] or number in gone)

    async def cut_off(voice_session: host_service.AnyVoice) -> None:
        if floor["speaking"] and not floor["cut"]:
            floor["cut"] = True
            await voice_session.interrupt()

    def line_id(data: dict) -> int:
        try:
            number = int(data.get("id") or 0)
        except (TypeError, ValueError):
            number = 0
        floor["seen"] = max(floor["seen"], number)
        return number

    async def reader(voice_session: host_service.AnyVoice) -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    chunk = message["bytes"]
                    if talk["open"] and talk["bytes"] + len(chunk) <= MAX_TALK_BYTES and len(chunk) % 2 == 0:
                        talk["bytes"] += len(chunk)
                        await voice_session.hear(chunk)
                    continue
                try:
                    data = json.loads(message.get("text") or "{}")
                except ValueError:
                    continue
                kind = data.get("type")
                if kind == "cue":
                    await actions.put(("cue", line_id(data), data))
                elif kind == "cancel":
                    try:
                        floor["void"] = max(floor["void"], int(data.get("through") or 0))
                        if data.get("only") is not None:
                            gone.add(int(data["only"]))
                    except (TypeError, ValueError):
                        continue
                    if unwanted(floor["speaking"]):
                        await cut_off(voice_session)
                elif kind == "talk.start" and can_talk:
                    talk.update(open=True, bytes=0)
                    floor["void"] = max(floor["void"], floor["seen"])      # the contestant has the floor now
                    await cut_off(voice_session)
                    await voice_session.forget_heard()
                elif kind == "talk.end" and talk["open"]:
                    talk["open"] = False
                    number = line_id(data)
                    if talk["bytes"] >= MIN_TALK_BYTES:
                        await actions.put(("talk", number, data))
                    else:
                        await voice_session.forget_heard()
                        await websocket.send_json({"type": "line.start", "id": number, "cue": "talk"})
                        await websocket.send_json({"type": "line.text", "id": number, "delta": "Didn't catch that — tap the mic and try again."})
                        await websocket.send_json({"type": "line.end", "id": number, "cut": False})
                elif kind == "talk.cancel":
                    talk["open"] = False
                    await voice_session.forget_heard()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await actions.put(("close", 0, None))

    reader_task: asyncio.Task | None = None
    try:
        async with host_service.open_voice(instructions, host, speak) as voice_session:
            reader_task = asyncio.create_task(reader(voice_session))
            await websocket.send_json({"type": "ready", "voice": speak, "talk": can_talk,
                                       "sampleRate": host_service.SAMPLE_RATE, "name": host["name"]})
            spoken = 0
            while True:
                kind, number, message = await actions.get()
                if kind == "close":
                    break

                stream = None
                within_budget = spoken < host_service.MAX_LINES_PER_CONNECTION and _said.get(game, 0) < MAX_LINES_PER_GAME
                if within_budget and not unwanted(number):
                    if kind == "talk":
                        current = await db.fetchrow("select * from runs where id = $1", run_row["id"])
                        stream = voice_session.reply(await host_service.talk_note(current, player))
                        cue = "talk"
                    else:
                        cue = str(message.get("cue", ""))
                        if session is not None:
                            fresh_session = await live.load(live_code, fresh=True)
                            if not live.is_host(fresh_session, live_key):
                                break
                            q_line = await _live_question_line(fresh_session) if cue == "live_question" else None
                            if q_line and not unwanted(number):
                                stream = voice_session.recite(q_line, note="Read the live question verbatim.", keep=False)
                                direction = None
                            else:
                                direction = await _live_direction(fresh_session, cue)
                        elif cue == "read":
                            # The final question, read out word for word (never cached globally so confidential
                            # quiz text does not persist after quiz deletion).
                            current = await db.fetchrow("select * from runs where id = $1", run_row["id"])
                            line = await host_service.final_question_line(
                                current, message.get("itemId"), manner=host.get("manner"),
                            )
                            direction = None
                            if line and not unwanted(number):
                                stream = voice_session.recite(line, keep=False)
                        else:
                            current = await db.fetchrow("select * from runs where id = $1", run_row["id"])
                            direction = await host_service.describe_cue(cue, run=current, player=player,
                                                                        item_id=message.get("itemId"))
                            # A round introduction comes from the host's rotating set, recorded once (see round_set).
                            if (cue == "round" and direction and speak
                                    and isinstance(voice_session, host_service.WrittenHostVoice)):
                                round_key = await host_service.round_set(current, message.get("itemId"))
                                line = await voice_session.from_set(round_key, direction) if round_key else None
                                if line and not unwanted(number):
                                    stream = voice_session.recite(line, note=direction, keep=True)
                                    direction = None
                        # Looking the facts up takes a moment: the player may have moved on meanwhile.
                        if direction and not unwanted(number):
                            stream = voice_session.say(direction)
                if stream is None:
                    if kind == "talk":
                        await voice_session.forget_heard()
                    await websocket.send_json({"type": "line.skip", "id": number})
                    continue

                spoken += 1
                _count_line(game)
                floor.update(speaking=number, cut=False)
                started = False
                if kind != "talk":
                    await websocket.send_json({"type": "line.start", "id": number, "cue": cue})
                    started = True
                words: list[str] = []
                try:
                    async for piece, data in stream:
                        if unwanted(number):
                            await cut_off(voice_session)   # stop the model; discard whatever is still in the pipe
                            break
                        if not started:
                            await websocket.send_json({"type": "line.start", "id": number, "cue": cue})
                            started = True
                        if piece == "audio":
                            if not talk["open"]:           # never talk over the contestant
                                await websocket.send_bytes(data)
                        else:
                            words.append(str(data))
                            await websocket.send_json({"type": "line.text", "id": number, "delta": data})
                finally:
                    was_cut, floor["speaking"] = floor["cut"], 0
                if started:
                    await websocket.send_json({"type": "line.end", "id": number, "cut": was_cut})
                elif kind == "talk" and not unwanted(number):
                    await websocket.send_json({"type": "line.start", "id": number, "cue": "talk"})
                    await websocket.send_json({"type": "line.text", "id": number, "delta": "Didn't catch that — tap the mic and try again."})
                    await websocket.send_json({"type": "line.end", "id": number, "cut": False})
                else:
                    await websocket.send_json({"type": "line.skip", "id": number})
                if cue == "welcome" and run_row is not None and words:
                    # Kept so that the player's next opening can be different (see host_service._welcome).
                    await db.execute("update runs set host_opening = $2 where id = $1", run_row["id"],
                                     " ".join("".join(words).split())[:600])
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 – the host is a garnish; the game must carry on without it
        log.warning("host channel closed: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": "The host has left the building."})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if reader_task is not None:
            reader_task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
