"""Live group play: one big screen, everyone answering on their own phone.

The whole session lives in one database row, so any app instance can serve any
player and nothing is lost if an instance restarts. Clients listen on a
server-sent-events stream (plain HTTPS, works through corporate proxies) and fall
back to polling the same state. Phase changes are applied lazily and atomically:
whoever notices the timer has run out moves the session on, guarded by a version
number so it only happens once.

Phases: lobby → question → reveal → scoreboard → (next question …) → finished

A question starts locked: it is on the big screen and the host reads it out, but the phones
only unlock – and the clock only starts – when the big screen says the host has finished
("open"). If that never arrives, the question opens by itself after a generous lead-in.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import db
from config import config
from security import hash_key, key_matches, new_host_key, new_join_code, sign_media
from services import bank, music_service, stats

OPEN_BEAT_SEC = 1.0        # between "open" and the phones unlocking: long enough for every phone to hear of it
MAX_LEAD_IN_SEC = 26       # however long the question, it opens by itself after this
MAX_PARTICIPANTS = 500


class LiveError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _image_url(image_id: str) -> str:
    expires, signature = sign_media(f"{image_id}:sharp", ttl_seconds=12 * 3600)
    return f"/api/media/image/{image_id}?v=sharp&exp={expires}&sig={signature}"


# --------------------------------------------------------------------------- creating

async def _line_up(quiz: dict, rules: dict, count: int, music_rounds: int, decade: str | None) -> list[dict]:
    """Choose the questions: must-asks first, then what the group found hardest in the pre-read, then fresh ones."""
    questions = [q for q in await bank.approved(quiz["id"], rules["question_sources"]) if q["kind"] == "mcq"]
    if len(questions) < 3:
        raise LiveError("This quiz needs at least three approved questions before a live game can start.", 409)
    served = {r["id"]: r for r in await db.fetch(
        "select id, times_served, times_correct from questions where quiz_id = $1", quiz["id"])}

    def priority(question: dict) -> tuple:
        record = served.get(question["id"], {"times_served": 0, "times_correct": 0})
        accuracy = record["times_correct"] / record["times_served"] if record["times_served"] >= 3 else 0.6
        return (not question["pinned"], accuracy + random.random() * 0.25)

    chosen: list[dict] = []
    per_section: dict[str, int] = {}
    pictures = 0
    for question in sorted(questions, key=priority):
        if len(chosen) >= count:
            break
        if per_section.get(question["section_id"], 0) >= max(2, count // 4) and not question["pinned"]:
            continue
        if not question["pinned"] and any(bank.same_fact(bank.key_terms(question), bank.key_terms(other)) for other in chosen):
            continue                                # the room has already been asked this, in other words
        if question["source_type"] == "image":
            if pictures >= 2:
                continue
            pictures += 1
        per_section[question["section_id"]] = per_section.get(question["section_id"], 0) + 1
        chosen.append(question)
    random.shuffle(chosen)
    chosen.sort(key=lambda q: {"easy": 0, "medium": 1, "hard": 2}[q["difficulty"]])

    headings = {s["id"]: s["heading"] for s in await bank.sections(quiz["id"])}
    timer = int(rules["live"]["timer_sec"])
    items: list[dict] = []
    for question in chosen:
        order = list(range(4))
        random.shuffle(order)
        items.append({
            "kind": "mcq", "question_id": question["id"], "title": question["title"], "text": question["question_text"],
            "options": [question["options"][i] for i in order], "correct_index": order.index(question["correct_index"]),
            "image_id": question["image_id"], "explanation": question["explanation"],
            "source_quote": question["source_quote"] if question["source_type"] != "image" else None,
            "section": headings.get(question["section_id"]), "limit": timer + (10 if question["image_id"] else 0),
        })

    if music_rounds and decade and rules["music_enabled"]:
        used: set[str] = set()
        step = max(2, len(items) // (music_rounds + 1))
        for round_index in range(music_rounds):
            built = await music_service.build_question(decade, used)
            if built is None:
                break
            track = built["track"]
            used.add(track["id"])
            items.insert(min(len(items), step * (round_index + 1) + round_index), {
                "kind": "music", "title": "Name That Tune", "text": built["text"], "options": built["options"],
                "correct_index": built["correct_index"], "limit": 20, "section": f"Music Break · {decade}",
                "music": {"preview_url": track["preview_url"], "artist": track["artist"], "title": track["title"],
                          "artwork_url": track["artwork_url"]},
            })
    return items


async def create_session(quiz: dict, rules: dict, decade: str | None = None) -> dict:
    cfg = rules["live"]
    after_party = bool(rules["music_enabled"] and rules.get("after_party", True))
    if decade not in rules["decades"]:
        wants_music = rules["music_enabled"] and (int(cfg["music_rounds"]) or after_party) and rules["decades"]
        decade = random.choice(rules["decades"]) if wants_music else None
    if decade:
        music_service.ensure_decade(decade)
    items = await _line_up(quiz, rules, int(cfg["questions"]), int(cfg["music_rounds"]), decade)
    host_key = new_host_key()
    for _ in range(8):
        code = new_join_code()
        if not await db.fetchval("select 1 from live_sessions where code = $1", code):
            break
    session = await db.fetchrow(
        "insert into live_sessions (quiz_id, code, host_key_hash, settings, items) values ($1,$2,$3,$4,$5) returning *",
        quiz["id"], code, hash_key(host_key),
        {"timer_sec": int(cfg["timer_sec"]), "head_start": int(cfg["head_start"]), "decade": decade,
         "ready_threshold": int(rules["ready_threshold"]), "after_party": after_party and bool(decade)}, items)
    base = config.web_base_url or config.public_base_url
    return {"code": session["code"], "hostKey": host_key, "questions": len(items),
            "hostUrl": f"{base}/host/{session['code']}#k={host_key}", "joinUrl": f"{base}/live/{session['code']}"}


# --------------------------------------------------------------------------- loading + transitions

_recent: dict[str, tuple[float, dict]] = {}


async def load(code: str, fresh: bool = False) -> dict:
    code = (code or "").strip().upper()
    cached = _recent.get(code)
    if not fresh and cached and time.monotonic() - cached[0] < 0.4:
        return cached[1]
    session = await db.fetchrow("select * from live_sessions where code = $1", code)
    if session is None:
        raise LiveError("No live game with that code.", 404)
    _recent[code] = (time.monotonic(), session)
    return session


def is_host(session: dict, key: str | None) -> bool:
    return bool(key) and key_matches(key, session["host_key_hash"])


async def _move(session: dict, **changes: Any) -> dict:
    """Apply a phase change only if nobody else has already moved the session on."""
    columns = ", ".join(f"{name} = ${i}" for i, name in enumerate(changes, start=3))
    row = await db.fetchrow(
        f"update live_sessions set {columns}, version = version + 1 where id = $1 and version = $2 returning *",
        session["id"], session["version"], *changes.values())
    _recent.pop(session["code"], None)
    return row or await load(session["code"], fresh=True)


def _lead_in(item: dict) -> float:
    """The longest the phones stay locked: time for the host to read the question out, with room to spare."""
    if item["kind"] == "music":
        return 10.0
    return min(MAX_LEAD_IN_SEC, 6 + len(item["text"].split()) / 2.0)


async def _start_question(session: dict, index: int) -> dict:
    item = session["items"][index]
    opens = _now() + timedelta(seconds=_lead_in(item))
    return await _move(session, status="question", current_index=index, phase_started_at=opens,
                       phase_ends_at=opens + timedelta(seconds=int(item["limit"])),
                       started_at=session["started_at"] or _now())


async def _open_question(session: dict) -> dict:
    """The host has finished reading: unlock the phones a beat from now and start the clock then."""
    opens = _now() + timedelta(seconds=OPEN_BEAT_SEC)
    if session["status"] != "question" or opens >= session["phase_started_at"]:
        return session                                  # already open, or about to be
    item = session["items"][session["current_index"]]
    return await _move(session, phase_started_at=opens, phase_ends_at=opens + timedelta(seconds=int(item["limit"])))


async def tick(session: dict) -> dict:
    """Move a question on to its reveal once time is up or everyone has answered."""
    if session["status"] != "question":
        return session
    if _now() >= session["phase_ends_at"]:
        return await _move(session, status="reveal", phase_started_at=_now(), phase_ends_at=None)
    if _now() >= session["phase_started_at"]:
        answered, joined = await _answer_counts(session)
        if joined and answered >= joined:
            return await _move(session, status="reveal", phase_started_at=_now(), phase_ends_at=None)
    return session


async def _answer_counts(session: dict) -> tuple[int, int]:
    row = await db.fetchrow(
        "select (select count(*) from live_answers where session_id = $1 and question_index = $2) as answered,"
        " (select count(*) from live_participants where session_id = $1) as joined",
        session["id"], session["current_index"])
    return row["answered"], row["joined"]


async def control(session: dict, action: str) -> dict:
    session = await load(session["code"], fresh=True)
    status, index, total = session["status"], session["current_index"], len(session["items"])
    if action == "end":
        return await _move(session, status="finished", finished_at=_now(), phase_ends_at=None)
    if action == "open":
        return await _open_question(session)
    if action == "next":
        if status == "lobby":
            return await _start_question(session, 0)
        if status == "question":
            return await _move(session, status="reveal", phase_started_at=_now(), phase_ends_at=None)
        if status == "reveal":
            return await _move(session, status="scoreboard", phase_started_at=_now(), phase_ends_at=None)
        if status == "scoreboard":
            if index + 1 >= total:
                return await _move(session, status="finished", finished_at=_now(), phase_ends_at=None)
            return await _start_question(session, index + 1)
        return session
    raise LiveError("Unknown action.")


# --------------------------------------------------------------------------- joining + answering

async def join(session: dict, player: dict) -> dict:
    existing = await db.fetchrow("select * from live_participants where session_id = $1 and player_id = $2",
                                 session["id"], player["id"])
    if existing:
        return existing
    if session["status"] == "finished":
        raise LiveError("This live game has finished.", 409)
    if await db.fetchval("select count(*) from live_participants where session_id = $1", session["id"]) >= MAX_PARTICIPANTS:
        raise LiveError("This live game is full.", 409)
    ready = await stats.readiness(session["quiz_id"], player["id"], int(session["settings"].get("ready_threshold", 80)))
    head_start = int(session["settings"].get("head_start", 0)) if ready["ready"] else 0
    participant = await db.fetchrow(
        "insert into live_participants (session_id, player_id, display_name, team, score, head_start)"
        " values ($1,$2,$3,$4,$5,$5) on conflict (session_id, player_id) do update set display_name = excluded.display_name"
        " returning *", session["id"], player["id"], stats.display_name(player["name"]), player["team"], head_start)
    await db.execute("update live_sessions set version = version + 1 where id = $1", session["id"])
    _recent.pop(session["code"], None)
    return participant


async def answer(session: dict, player: dict, choice: int) -> dict:
    session = await tick(await load(session["code"], fresh=True))
    if session["status"] != "question":
        raise LiveError("Too late – that question has closed.", 409)
    now = _now()
    if now < session["phase_started_at"]:
        raise LiveError("Not open yet.", 409)
    participant = await db.fetchrow("select * from live_participants where session_id = $1 and player_id = $2",
                                    session["id"], player["id"])
    if participant is None:
        raise LiveError("Join the game first.", 403)
    index = session["current_index"]
    item = session["items"][index]
    if not (0 <= choice < len(item["options"])):
        raise LiveError("That is not one of the options.")
    elapsed = (now - session["phase_started_at"]).total_seconds()
    fraction = max(0.0, min(1.0, 1 - elapsed / max(1, int(item["limit"]))))
    correct = choice == item["correct_index"]
    points = (500 + round(500 * fraction) + 100 * min(participant["streak"], 5)) if correct else 0
    inserted = await db.fetchrow(
        "insert into live_answers (session_id, participant_id, question_index, selected_index, is_correct, time_ms, points)"
        " values ($1,$2,$3,$4,$5,$6,$7) on conflict (session_id, participant_id, question_index) do nothing returning id",
        session["id"], participant["id"], index, choice, correct, int(elapsed * 1000), points)
    if inserted:
        await db.execute(
            "update live_participants set score = score + $2, correct_count = correct_count + $3,"
            " streak = case when $4 then streak + 1 else 0 end where id = $1",
            participant["id"], points, int(correct), correct)
    return {"locked": True, "selectedIndex": choice}


# --------------------------------------------------------------------------- state for screens

async def _standings(session_id: str) -> list[dict]:
    rows = await db.fetch(
        "select id, player_id, display_name, team, score, correct_count, streak, head_start from live_participants"
        " where session_id = $1 order by score desc, joined_at asc", session_id)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def _teams(standings: list[dict]) -> list[dict]:
    groups: dict[str, list[int]] = {}
    for row in standings:
        if row["team"]:
            groups.setdefault(row["team"], []).append(row["score"])
    ranked = [{"team": team, "average": round(sum(scores) / len(scores)), "players": len(scores)}
              for team, scores in groups.items()]
    return sorted(ranked, key=lambda t: -t["average"]) if len(ranked) >= 2 else []


async def state(session: dict, *, host: bool, player: dict | None) -> dict:
    session = await tick(session)
    status, index, items = session["status"], session["current_index"], session["items"]
    item = items[index] if 0 <= index < len(items) else None
    quiz = await db.fetchrow("select title, slug from quizzes where id = $1", session["quiz_id"])
    standings = await _standings(session["id"])
    me = next((s for s in standings if player and s["player_id"] == player["id"]), None)
    view: dict[str, Any] = {
        "code": session["code"], "status": status, "version": session["version"], "index": index,
        "total": len(items), "quizTitle": quiz["title"], "quizSlug": quiz["slug"], "players": len(standings),
        "serverNow": _now().isoformat(), "role": "host" if host else "player",
        "decade": session["settings"].get("decade"),
    }
    if me:
        view["you"] = {"name": me["display_name"], "score": me["score"], "rank": me["rank"], "streak": me["streak"],
                       "headStart": me["head_start"]}
    elif not host:
        view["you"] = None

    if status == "lobby":
        view["roster"] = [{"name": s["display_name"], "team": s["team"], "prepared": s["head_start"] > 0}
                          for s in standings][:120 if host else 30]
        view["headStart"] = int(session["settings"].get("head_start", 0))
        return view

    if item and status in ("question", "reveal"):
        question: dict[str, Any] = {
            "number": index + 1, "kind": item["kind"], "title": item.get("title"), "text": item["text"],
            "options": item["options"], "limitSec": int(item["limit"]), "section": item.get("section"),
            "opensAt": session["phase_started_at"].isoformat() if status == "question" else None,
            "endsAt": session["phase_ends_at"].isoformat() if session["phase_ends_at"] else None,
        }
        if item.get("image_id"):
            question["imageUrl"] = _image_url(item["image_id"])
        if item["kind"] == "music" and host:
            question["previewUrl"] = item["music"]["preview_url"]   # only the big screen plays the clip
        view["question"] = question
        mine = None
        if me:
            mine = await db.fetchrow(
                "select selected_index, is_correct, points from live_answers where session_id = $1"
                " and participant_id = $2 and question_index = $3", session["id"], me["id"], index)
        if status == "question":
            if host:
                view["answersIn"] = (await _answer_counts(session))[0]
            elif mine:
                view["yourAnswer"] = {"selectedIndex": mine["selected_index"]}
        else:
            distribution = await db.fetch(
                "select selected_index, count(*) as n from live_answers where session_id = $1 and question_index = $2"
                " group by selected_index", session["id"], index)
            counts = [0] * len(item["options"])
            for row in distribution:
                if row["selected_index"] is not None:
                    counts[row["selected_index"]] = row["n"]
            view["reveal"] = {"correctIndex": item["correct_index"], "counts": counts,
                              "explanation": item.get("explanation"), "sourceQuote": item.get("source_quote")}
            if item["kind"] == "music":
                view["reveal"]["music"] = {"artist": item["music"]["artist"], "title": item["music"]["title"],
                                           "artworkUrl": item["music"]["artwork_url"]}
            if not host:
                view["yourAnswer"] = ({"selectedIndex": mine["selected_index"], "correct": mine["is_correct"],
                                       "points": mine["points"]} if mine else {"selectedIndex": None, "correct": False, "points": 0})
        return view

    top = [{"rank": s["rank"], "name": s["display_name"], "team": s["team"], "score": s["score"],
            "streak": s["streak"]} for s in standings[:10 if status == "finished" else 5]]
    view["standings"] = top
    view["teams"] = _teams(standings)
    if status == "finished":
        totals = await db.fetch(
            "select question_index, count(*) as answered, count(*) filter (where is_correct) as correct"
            " from live_answers where session_id = $1 group by question_index", session["id"])
        missed = []
        for row in totals:
            source = items[row["question_index"]]
            if source["kind"] != "mcq" or not row["answered"]:
                continue
            missed.append({"text": source["text"], "correctAnswer": source["options"][source["correct_index"]],
                           "section": source.get("section"), "explanation": source.get("explanation"),
                           "percentCorrect": round(100 * row["correct"] / row["answered"])})
        view["roomMissed"] = sorted(missed, key=lambda m: m["percentCorrect"])[:3]
        if host and session["settings"].get("after_party") and session["settings"].get("decade"):
            # Music for the room while the results are up (the same list every time this is asked for).
            decade = session["settings"]["decade"]
            tracks = await music_service.after_party(decade, seed=session["id"])
            if tracks:
                view["afterParty"] = {"decade": decade, "tracks": tracks}
    return view
