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
from services import bank, music_service, stats, video

OPEN_BEAT_SEC = 1.0        # between "open" and the phones unlocking: long enough for every phone to hear of it
MAX_LEAD_IN_SEC = 26       # however long the question, it opens by itself after this
MAX_PARTICIPANTS = 500


def is_double_points(index: int, total: int) -> bool:
    """The final two questions of a live game pay 2x points so the race stays alive to the end."""
    return total >= 4 and index >= total - 2


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
    """Choose a balanced easy -> medium -> hard deck: must-asks first, stratified by difficulty."""
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
    chosen_ids: set[str] = set()
    per_section: dict[str, int] = {}
    pictures = 0

    def _can_take(question: dict, relax_section: bool = False) -> bool:
        if question["id"] in chosen_ids:
            return False
        if not relax_section and per_section.get(question["section_id"], 0) >= max(2, count // 4) and not question["pinned"]:
            return False
        if not question["pinned"] and any(bank.same_fact(bank.key_terms(question), bank.key_terms(other)) for other in chosen):
            return False
        if question["source_type"] == "image" and pictures >= 4:
            return False
        return True

    def _take(question: dict) -> None:
        nonlocal pictures
        if question["source_type"] == "image":
            pictures += 1
        per_section[question["section_id"]] = per_section.get(question["section_id"], 0) + 1
        chosen_ids.add(question["id"])
        chosen.append(question)

    # 1) Pinned must-asks always make the deck first.
    for q in sorted([q for q in questions if q["pinned"]], key=priority):
        if len(chosen) >= count:
            break
        if _can_take(q):
            _take(q)

    # 2) Stratified easy (~30%) / medium (~50%) / hard (~20%) quotas so live rooms ramp smoothly instead of picking the 10 hardest Qs.
    remaining = max(0, count - len(chosen))
    quotas = {
        "easy": max(1, round(remaining * 0.30)) if remaining >= 3 else remaining,
        "medium": max(1, round(remaining * 0.50)) if remaining >= 3 else 0,
        "hard": max(0, remaining - max(1, round(remaining * 0.30)) - max(1, round(remaining * 0.50))) if remaining >= 3 else 0,
    }
    for diff, quota in quotas.items():
        taken = 0
        for q in sorted([q for q in questions if q["difficulty"] == diff], key=priority):
            if taken >= quota or len(chosen) >= count:
                break
            if _can_take(q):
                _take(q)
                taken += 1

    # 3) Fill any leftover slots from any difficulty (relaxing section cap only if needed).
    for relax in (False, True):
        for q in sorted(questions, key=priority):
            if len(chosen) >= count:
                break
            if _can_take(q, relax_section=relax):
                _take(q)

    random.shuffle(chosen)
    chosen.sort(key=lambda q: {"easy": 0, "medium": 1, "hard": 2}[q["difficulty"]])

    headings = {s["id"]: s["heading"] for s in await bank.sections(quiz["id"])}
    timer = int(rules["live"]["timer_sec"])
    items: list[dict] = []
    for question in chosen:
        order = list(range(len(question["options"])))
        random.shuffle(order)
        items.append({
            "kind": "mcq", "question_id": question["id"], "title": question["title"], "text": question["question_text"],
            "options": [question["options"][i] for i in order], "correct_index": order.index(question["correct_index"]),
            "image_id": question["image_id"],
            "reveal_image_id": question.get("reveal_image_id"),
            "video_url": question.get("video_url"),
            "video_title": question.get("video_title"),
            "explanation": question["explanation"],
            "source_quote": question["source_quote"] if question["source_type"] != "image" else None,
            "section": headings.get(question["section_id"]), "limit": timer + (10 if question["image_id"] else 0),
        })

    approved_only, pinned_ids = music_service.music_filter(rules)
    if music_rounds and (decade or pinned_ids) and rules["music_enabled"]:
        used: set[str] = set()
        step = max(2, len(items) // (music_rounds + 1))
        for round_index in range(music_rounds):
            built = await music_service.build_question(
                decade, used, approved_only=approved_only, pinned_ids=pinned_ids,
            )
            if built is None:
                break
            track = built["track"]
            used.add(track["id"])
            era_label = "Curated" if pinned_ids else music_service.DECADE_NAMES.get(decade or "", decade or "")
            items.insert(min(len(items), step * (round_index + 1) + round_index), {
                "kind": "music", "title": "Name That Tune", "text": built["text"], "options": built["options"],
                "correct_index": built["correct_index"], "limit": 20,
                "section": f"Music Break · {era_label}" if era_label else "Music Break",
                "music": {"preview_url": track["preview_url"], "artist": track["artist"], "title": track["title"],
                          "artwork_url": track["artwork_url"]},
            })
    return items


async def create_session(quiz: dict, rules: dict, decade: str | None = None) -> dict:
    cfg = rules["live"]
    approved_only, pinned_ids = music_service.music_filter(rules)
    after_party = bool(rules["music_enabled"] and rules.get("after_party", True))
    if decade not in rules["decades"]:
        wants_music = rules["music_enabled"] and (int(cfg["music_rounds"]) or after_party) and rules["decades"]
        decade = random.choice(rules["decades"]) if wants_music else None
    if decade and not (approved_only or pinned_ids):
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
        {
            "timer_sec": int(cfg["timer_sec"]),
            "head_start": int(cfg["head_start"]),
            "decade": decade,
            "ready_threshold": int(rules["ready_threshold"]),
            "after_party": after_party and bool(decade or pinned_ids),
            "approved_tracks_only": approved_only,
            "pinned_track_ids": pinned_ids or [],
            "unlisted": bool(rules.get("unlisted", False)),
            "leaderboard": bool(rules.get("leaderboard", True)),
        }, items)
    base = config.web_base_url or config.public_base_url
    return {"code": session["code"], "hostKey": host_key, "questions": len(items),
            "hostUrl": f"{base}/host/{session['code']}#k={host_key}", "joinUrl": f"{base}/live/{session['code']}"}


async def reclaim_session(session_id: str) -> dict:
    """Rotate the host control key for an active live session so closing a projector tab never bricks a meeting."""
    session = await db.fetchrow("select * from live_sessions where id = $1", session_id)
    if session is None:
        raise LiveError("We could not find that live game.", 404)
    if session["status"] == "finished":
        raise LiveError("That live game has already finished.", 409)
    host_key = new_host_key()
    await db.execute("update live_sessions set host_key_hash = $2 where id = $1", session["id"], hash_key(host_key))
    _recent.pop(session["code"], None)
    _public_snap.pop(session["id"], None)
    base = config.web_base_url or config.public_base_url
    return {"code": session["code"], "hostKey": host_key,
            "hostUrl": f"{base}/host/{session['code']}#k={host_key}", "joinUrl": f"{base}/live/{session['code']}"}


async def finish_session(session_id: str) -> dict:
    """Forcibly end an active live session (e.g. an abandoned test room) so it no longer shows as live."""
    session = await db.fetchrow("select id, quiz_id, code, status from live_sessions where id = $1", session_id)
    if session is None:
        raise LiveError("We could not find that live game.", 404)
    if session["status"] != "finished":
        await db.execute(
            "update live_sessions set status = 'finished', finished_at = coalesce(finished_at, now()),"
            " phase_ends_at = null, version = version + 1 where id = $1",
            session["id"],
        )
        _recent.pop(session["code"], None)
        _public_snap.pop(session["id"], None)
    return {"ok": True, "quizId": session["quiz_id"], "code": session["code"]}


# --------------------------------------------------------------------------- loading + transitions

_recent: dict[str, tuple[float, dict]] = {}
_public_snap: dict[str, tuple[int, float, dict[str, Any]]] = {}


async def load(code: str, fresh: bool = False) -> dict:
    code = (code or "").strip().upper()
    cached = _recent.get(code)
    if not fresh and cached and time.monotonic() - cached[0] < 0.8:
        return cached[1]
    session = await db.fetchrow("select * from live_sessions where code = $1", code)
    if session is None:
        raise LiveError("No live game with that code.", 404)
    _recent[code] = (time.monotonic(), session)
    while len(_recent) > 500:
        _recent.pop(next(iter(_recent)))
    return session


def is_host(session: dict, key: str | None) -> bool:
    return bool(key) and key_matches(key, session["host_key_hash"])


async def is_participant(session_id: str, player_id: str) -> bool:
    return bool(await db.fetchval(
        "select 1 from live_participants where session_id = $1 and player_id = $2", session_id, player_id,
    ))


async def _move(session: dict, **changes: Any) -> dict:
    """Apply a phase change only if nobody else has already moved the session on."""
    columns = ", ".join(f"{name} = ${i}" for i, name in enumerate(changes, start=3))
    row = await db.fetchrow(
        f"update live_sessions set {columns}, version = version + 1 where id = $1 and version = $2 returning *",
        session["id"], session["version"], *changes.values())
    _recent.pop(session["code"], None)
    _public_snap.pop(session["id"], None)
    return row or await load(session["code"], fresh=True)


async def _reveal_question(session: dict) -> dict:
    """Atomically move a question to reveal, commit scores/streaks for that question, and reset non-answerers' streaks.

    Committing participant scores inside the reveal transaction (rather than on submit) eliminates both
    the last-millisecond answer-vs-reveal race and the mid-question phone score spoiler.
    """
    async with db.transaction() as tx:
        locked = await tx.fetchrow("select * from live_sessions where id = $1 for update", session["id"])
        if locked is None or locked["status"] != "question":
            return locked or await load(session["code"], fresh=True)
        q_index = locked["current_index"]
        updated = await tx.fetchrow(
            "update live_sessions set status = 'reveal', phase_started_at = $2, phase_ends_at = null,"
            " version = version + 1 where id = $1 returning *",
            locked["id"], _now(),
        )
        if q_index >= 0:
            # Apply points and streaks for everyone who answered this question.
            await tx.execute(
                "update live_participants lp set"
                " score = lp.score + la.points,"
                " correct_count = lp.correct_count + case when la.is_correct then 1 else 0 end,"
                " streak = case when la.is_correct then lp.streak + 1 else 0 end"
                " from live_answers la"
                " where la.session_id = $1 and la.question_index = $2 and la.participant_id = lp.id",
                locked["id"], q_index,
            )
            # Reset streaks for anyone who sat the question out.
            await tx.execute(
                "update live_participants set streak = 0 where session_id = $1"
                " and id not in (select participant_id from live_answers where session_id = $1 and question_index = $2)",
                locked["id"], q_index,
            )
    _recent.pop(session["code"], None)
    _public_snap.pop(session["id"], None)
    _counts.pop(session["id"], None)
    return updated


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
        return await _reveal_question(session)
    if _now() >= session["phase_started_at"]:
        answered, joined = await _answer_counts(session)
        if joined and answered >= joined:
            return await _reveal_question(session)
    return session


_counts: dict[str, tuple[float, int, tuple[int, int]]] = {}


async def _answer_counts(session: dict) -> tuple[int, int]:
    """(answers in, players joined) for the current question.

    Every connected phone asks this twice a second while a question is open, so the answer is
    shared for a moment rather than counted afresh for each of them.
    """
    cached = _counts.get(session["id"])
    if cached and cached[1] == session["current_index"] and time.monotonic() - cached[0] < 0.8:
        return cached[2]
    counts = await _count_answers(session)
    _counts[session["id"]] = (time.monotonic(), session["current_index"], counts)
    while len(_counts) > 500:
        _counts.pop(next(iter(_counts)))
    return counts


async def _count_answers(session: dict) -> tuple[int, int]:
    row = await db.fetchrow(
        "select (select count(*) from live_answers where session_id = $1 and question_index = $2) as answered,"
        " (select count(*) from live_participants where session_id = $1) as joined",
        session["id"], session["current_index"])
    return row["answered"], row["joined"]


async def control(
    session: dict,
    action: str,
    *,
    expected_status: str | None = None,
    expected_index: int | None = None,
) -> dict:
    session = await load(session["code"], fresh=True)
    status, index, total = session["status"], session["current_index"], len(session["items"])
    if action == "end":
        return await _move(session, status="finished", finished_at=_now(), phase_ends_at=None)
    if action == "open":
        return await _open_question(session)
    if action == "extend":
        if status != "question" or session["phase_ends_at"] is None:
            return session
        # Add 15 seconds to the active countdown so presenters can handle room discussions smoothly.
        new_end = max(_now(), session["phase_ends_at"]) + timedelta(seconds=15)
        return await _move(session, phase_ends_at=new_end)
    if action == "next":
        if expected_status is not None and status != expected_status:
            return session
        if expected_index is not None and index != expected_index:
            return session
        if status == "lobby":
            return await _start_question(session, 0)
        if status == "question":
            return await _reveal_question(session)
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
    _counts.pop(session["id"], None)
    _public_snap.pop(session["id"], None)
    return participant


async def answer(session: dict, player: dict, choice: int, *, expected_index: int | None = None) -> dict:
    session = await tick(await load(session["code"], fresh=True))
    now = _now()
    async with db.transaction() as tx:
        locked = await tx.fetchrow("select * from live_sessions where id = $1 for update", session["id"])
        if locked is None or locked["status"] != "question":
            raise LiveError("Too late – that question has closed.", 409)
        index = locked["current_index"]
        if expected_index is not None and expected_index != index:
            raise LiveError("That question has already moved on.", 409)
        if now < locked["phase_started_at"]:
            raise LiveError("Not open yet.", 425)
        item = locked["items"][index]
        if not (0 <= choice < len(item["options"])):
            raise LiveError("That is not one of the options.")
        elapsed = max(0.0, (now - locked["phase_started_at"]).total_seconds())
        fraction = max(0.0, min(1.0, 1 - elapsed / max(1, int(item["limit"]))))
        correct = choice == item["correct_index"]
        mult = 2 if is_double_points(index, len(locked["items"])) else 1
        participant = await tx.fetchrow(
            "select * from live_participants where session_id = $1 and player_id = $2 for update",
            locked["id"], player["id"],
        )
        if participant is None:
            raise LiveError("Join the game first.", 403)
        # Scores are applied atomically in _reveal_question so no player sees a mid-question score spoiler.
        points = (500 + round(500 * fraction) + 100 * min(participant["streak"], 5)) * mult if correct else 0
        time_ms = int(elapsed * 1000)
        inserted = await tx.fetchrow(
            "insert into live_answers (session_id, participant_id, question_index, selected_index, is_correct, time_ms, points)"
            " values ($1,$2,$3,$4,$5,$6,$7) on conflict (session_id, participant_id, question_index) do nothing"
            " returning selected_index, time_ms",
            locked["id"], participant["id"], index, choice, correct, time_ms, points)
        if inserted is None:
            existing = await tx.fetchrow(
                "select selected_index, time_ms from live_answers"
                " where session_id = $1 and participant_id = $2 and question_index = $3",
                locked["id"], participant["id"], index,
            )
            chosen = existing["selected_index"] if existing else choice
            locked_ms = int(existing["time_ms"]) if existing and existing["time_ms"] is not None else time_ms
        else:
            chosen = inserted["selected_index"]
            locked_ms = int(inserted["time_ms"])
    if inserted:
        _counts.pop(session["id"], None)
    answered, joined = await _answer_counts(locked)
    return {"locked": True, "selectedIndex": chosen, "timeMs": locked_ms, "answersIn": answered, "players": joined}


# --------------------------------------------------------------------------- state for screens

async def _standings(session_id: str) -> list[dict]:
    rows = await db.fetch(
        "select id, player_id, display_name, team, score, correct_count, streak, head_start, joined_at"
        " from live_participants where session_id = $1 order by score desc, correct_count desc, joined_at asc",
        session_id)
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


async def _shared_snapshot(session: dict) -> dict[str, Any]:
    """Build the version-scoped shared view once per phase change so 500 SSE clients don't stampede DB."""
    sid, version = session["id"], session["version"]
    cached = _public_snap.get(sid)
    if cached and cached[0] == version and time.monotonic() - cached[1] < 2.0:
        return cached[2]

    status, index, items = session["status"], session["current_index"], session["items"]
    item = items[index] if 0 <= index < len(items) else None
    quiz = await db.fetchrow("select title, slug from quizzes where id = $1", session["quiz_id"])
    standings = await _standings(sid)
    snap: dict[str, Any] = {"quiz": quiz, "standings": standings}

    if item and status == "reveal":
        distribution = await db.fetch(
            "select selected_index, count(*) as n from live_answers where session_id = $1 and question_index = $2"
            " group by selected_index", sid, index)
        counts = [0] * len(item["options"])
        for row in distribution:
            if row["selected_index"] is not None:
                counts[row["selected_index"]] = row["n"]
        fastest_row = await db.fetchrow(
            "select lp.display_name, la.time_ms from live_answers la"
            " join live_participants lp on lp.id = la.participant_id"
            " where la.session_id = $1 and la.question_index = $2 and la.is_correct"
            " order by la.time_ms asc limit 1", sid, index)
        snap["counts"] = counts
        snap["fastest"] = (
            {"name": fastest_row["display_name"], "timeMs": int(fastest_row["time_ms"])} if fastest_row else None
        )

    if status == "scoreboard" and index >= 0:
        round_rows = await db.fetch(
            "select participant_id, points from live_answers where session_id = $1 and question_index = $2",
            sid, index)
        round_points_by_id = {r["participant_id"]: int(r["points"]) for r in round_rows}
        prev_order = sorted(standings, key=lambda s: (-(s["score"] - round_points_by_id.get(s["id"], 0)), s["rank"]))
        prev_rank = {s["id"]: i for i, s in enumerate(prev_order, start=1)}
        rank_delta_by_id: dict[str, int] = {}
        biggest_mover: dict[str, Any] | None = None
        for s in standings:
            delta = prev_rank[s["id"]] - s["rank"]
            rank_delta_by_id[s["id"]] = delta
            if delta > 0 and (biggest_mover is None or delta > biggest_mover["delta"]
                              or (delta == biggest_mover["delta"] and round_points_by_id.get(s["id"], 0) > biggest_mover["points"])):
                biggest_mover = {"name": s["display_name"], "delta": delta, "rank": s["rank"],
                                 "points": round_points_by_id.get(s["id"], 0)}
        snap["roundPoints"] = round_points_by_id
        snap["rankDelta"] = rank_delta_by_id
        snap["biggestMover"] = biggest_mover

    if status == "finished":
        totals = await db.fetch(
            "select question_index, count(*) as answered, count(*) filter (where is_correct) as correct"
            " from live_answers where session_id = $1 group by question_index", sid)
        missed = []
        for row in totals:
            source = items[row["question_index"]]
            if source["kind"] != "mcq" or not row["answered"]:
                continue
            pct = round(100 * row["correct"] / row["answered"])
            if pct < 75:
                missed.append({"text": source["text"], "correctAnswer": source["options"][source["correct_index"]],
                               "section": source.get("section"), "explanation": source.get("explanation"),
                               "percentCorrect": pct})
        snap["roomMissed"] = sorted(missed, key=lambda m: m["percentCorrect"])[:3]
        if session["settings"].get("after_party") and (session["settings"].get("decade") or session["settings"].get("pinned_track_ids")):
            decade = session["settings"].get("decade")
            tracks = await music_service.after_party(
                decade,
                seed=sid,
                approved_only=bool(session["settings"].get("approved_tracks_only")),
                pinned_ids=session["settings"].get("pinned_track_ids") or None,
            )
            if tracks:
                snap["afterParty"] = {"decade": decade, "tracks": tracks}

    _public_snap[sid] = (version, time.monotonic(), snap)
    while len(_public_snap) > 300:
        _public_snap.pop(next(iter(_public_snap)))
    return snap


async def state(session: dict, *, host: bool, player: dict | None) -> dict:
    session = await tick(session)
    status, index, items = session["status"], session["current_index"], session["items"]
    item = items[index] if 0 <= index < len(items) else None
    snap = await _shared_snapshot(session)
    quiz = snap["quiz"]
    standings = snap["standings"]
    me = next((s for s in standings if player and s["player_id"] == player["id"]), None)
    unlisted = bool(session["settings"].get("unlisted"))
    view: dict[str, Any] = {
        "code": session["code"], "status": status, "version": session["version"], "index": index,
        "total": len(items), "quizTitle": quiz["title"],
        "quizSlug": quiz["slug"] if (host or not unlisted) else None,
        "players": len(standings),
        "serverNow": _now().isoformat(), "role": "host" if host else "player",
        "decade": session["settings"].get("decade"),
    }
    if me:
        you_view: dict[str, Any] = {
            "name": me["display_name"], "score": me["score"], "rank": me["rank"], "streak": me["streak"],
            "headStart": me["head_start"],
        }
        if me["rank"] > 1:
            ahead = standings[me["rank"] - 2]
            you_view["gapToNext"] = max(0, ahead["score"] - me["score"])
            you_view["aheadName"] = ahead["display_name"]
        view["you"] = you_view
    elif not host:
        view["you"] = None

    if status == "lobby":
        roster_rows = sorted(standings, key=lambda s: s["joined_at"])
        view["roster"] = [{"id": str(s["id"]), "name": s["display_name"], "team": s["team"], "prepared": s["head_start"] > 0}
                          for s in roster_rows][:120 if host else 30]
        view["headStart"] = int(session["settings"].get("head_start", 0))
        return view

    if item and status in ("question", "reveal"):
        question: dict[str, Any] = {
            "number": index + 1, "kind": item["kind"], "title": item.get("title"), "text": item["text"],
            "options": item["options"], "limitSec": int(item["limit"]), "section": item.get("section"),
            "doublePoints": is_double_points(index, len(items)),
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
                "select selected_index, is_correct, points, time_ms from live_answers where session_id = $1"
                " and participant_id = $2 and question_index = $3", session["id"], me["id"], index)
        if status == "question":
            answered_count, _ = await _answer_counts(session)
            if host:
                view["answersIn"] = answered_count
            elif mine:
                view["answersIn"] = answered_count
                view["yourAnswer"] = {
                    "selectedIndex": mine["selected_index"],
                    "timeMs": int(mine["time_ms"]) if mine["time_ms"] is not None else None,
                }
        else:
            reveal_obj: dict[str, Any] = {
                "correctIndex": item["correct_index"], "counts": snap.get("counts", [0] * len(item["options"])),
                "explanation": item.get("explanation"), "sourceQuote": item.get("source_quote"),
                "fastest": snap.get("fastest"),
            }
            if not item.get("image_id") and item.get("reveal_image_id"):
                reveal_obj["imageUrl"] = _image_url(item["reveal_image_id"])
            clip = video.parse_youtube(item.get("video_url"), item.get("video_title"))
            if clip:
                reveal_obj["video"] = clip
            if item["kind"] == "music":
                reveal_obj["music"] = {"artist": item["music"]["artist"], "title": item["music"]["title"],
                                       "artworkUrl": item["music"]["artwork_url"]}
            view["reveal"] = reveal_obj
            if not host:
                fastest = snap.get("fastest")
                is_fastest = bool(
                    mine and mine["is_correct"] and fastest and me
                    and fastest["name"] == me["display_name"]
                    and mine["time_ms"] is not None and int(mine["time_ms"]) == fastest["timeMs"]
                )
                view["yourAnswer"] = (
                    {
                        "selectedIndex": mine["selected_index"], "correct": mine["is_correct"],
                        "points": mine["points"],
                        "timeMs": int(mine["time_ms"]) if mine["time_ms"] is not None else None,
                        "isFastest": is_fastest,
                    }
                    if mine else {"selectedIndex": None, "correct": False, "points": 0}
                )
        return view

    round_points_by_id: dict[str, int] = snap.get("roundPoints", {})
    rank_delta_by_id: dict[str, int] = snap.get("rankDelta", {})
    biggest_mover: dict[str, Any] | None = snap.get("biggestMover")

    def _standing_row(s: dict) -> dict[str, Any]:
        row: dict[str, Any] = {"rank": s["rank"], "name": s["display_name"], "team": s["team"], "score": s["score"],
                               "streak": s["streak"]}
        if status == "scoreboard":
            if round_points_by_id.get(s["id"], 0) > 0:
                row["roundPoints"] = round_points_by_id[s["id"]]
            if rank_delta_by_id.get(s["id"], 0) != 0:
                row["rankDelta"] = rank_delta_by_id[s["id"]]
        if me is not None:
            row["you"] = s["player_id"] == me["player_id"]
        return row

    cap = (16 if status == "finished" else 10) if host else 25
    view["standings"] = [_standing_row(s) for s in standings[:cap]]
    if biggest_mover:
        view["biggestMover"] = biggest_mover
    if me is not None and not host and me["rank"] > cap:
        idx = me["rank"] - 1
        start = max(cap, idx - 1)
        view["aroundYou"] = [_standing_row(s) for s in standings[start:idx + 2]]
    view["teams"] = _teams(standings)
    if status == "finished":
        view["roomMissed"] = snap.get("roomMissed", [])
        if host and snap.get("afterParty"):
            view["afterParty"] = snap["afterParty"]
    return view
