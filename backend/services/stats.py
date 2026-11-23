"""Results: leaderboards, readiness, end-of-game summaries and admin insight."""
from __future__ import annotations

import csv
import io
import math
from typing import Any

import db
from services import badges, favorites, bank, music_service, video
from services.settings_store import MODES


def display_name(name: str) -> str:
    """'Dana Reyes' -> 'Dana R.' so leaderboards do not publish full names."""
    parts = (name or "Player").split()
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0].upper()}."


async def askable_sections(quiz_id: str) -> list[dict]:
    """Sections that have at least one live question – the denominator for readiness."""
    return await db.fetch(
        "select s.id, s.heading, s.ordinal from doc_sections s join documents d on d.id = s.document_id"
        " where s.quiz_id = $1 and exists (select 1 from questions q where q.section_id = s.id and q.status = 'approved')"
        " order by d.created_at, s.ordinal", quiz_id)


# A section is cleared when the player knows it now: of their latest answers in it (up to RECENT), CLEAR_SHARE
# right - 2/2, 3/3, 3/4 or 4/4 - or, with only one answer so far, a right multiple-choice one (a Straight Quiz asks
# each section about once; a lone Fact or Fake call is a coin toss). Missing it again takes it away.
# (It used to be one right answer ever, in any game: a player on 12% was told they were ready with 8/8 sections,
# from lucky guesses in earlier games. User, 2026-11-21.)
RECENT = 4
CLEAR_SHARE = 0.75

_SECTION_ANSWERS = (
    "with answered as (select ri.player_id, ri.section_id, ri.is_correct,"
    "   ri.slot, row_number() over (partition by ri.player_id, ri.section_id order by ri.answered_at desc) as n"
    "   from run_items ri join runs r on r.id = ri.run_id"
    "   where ri.quiz_id = $1 and ri.section_id is not null"
    "   and ri.slot not in ('music', 'jargon', 'song_or_jargon', 'second_chance')"
    "   and not ('peek' = any(ri.lifelines_used))"
    "   and r.mode <> 'speed'"
    "   and ri.answered_at is not null"
    "   {player})"
    " select player_id, section_id, count(*) as answered, count(*) filter (where is_correct) as correct,"
    " count(*) filter (where n <= {recent}) as recent_answered,"
    " count(*) filter (where n <= {recent} and is_correct) as recent_correct,"
    " count(*) filter (where n <= {recent} and is_correct and slot <> 'fact_fake') as recent_choice_correct"
    " from answered group by player_id, section_id")


def _cleared(row: dict | None) -> bool:
    if not row or not row["recent_answered"]:
        return False
    # Require at least one right multiple-choice answer so pure 50/50 Fact-or-Fake coin flips never clear a section.
    if not row.get("recent_choice_correct"):
        return False
    if row["recent_answered"] == 1:
        return row["recent_choice_correct"] == 1
    return row["recent_correct"] >= CLEAR_SHARE * row["recent_answered"]


async def section_states(quiz_id: str, player_id: str) -> dict[str, str]:
    """section_id -> 'cleared' | 'tried' | 'new' for steering replays toward blind spots."""
    sections = await askable_sections(quiz_id)
    rows = await db.fetch(_SECTION_ANSWERS.format(player="and ri.player_id = $2", recent=RECENT), quiz_id, player_id)
    by_section = {r["section_id"]: r for r in rows}
    return {
        s["id"]: ("cleared" if _cleared(by_section.get(s["id"]))
                  else ("tried" if by_section.get(s["id"]) and by_section[s["id"]]["answered"] else "new"))
        for s in sections
    }


async def readiness(quiz_id: str, player_id: str, threshold: int) -> dict:
    sections = await askable_sections(quiz_id)
    rows = await db.fetch(_SECTION_ANSWERS.format(player="and ri.player_id = $2", recent=RECENT), quiz_id, player_id)
    by_section = {r["section_id"]: r for r in rows}
    detail = []
    for section in sections:
        stats = by_section.get(section["id"])
        state = "cleared" if _cleared(stats) else ("tried" if stats and stats["answered"] else "new")
        detail.append({"id": section["id"], "heading": section["heading"], "state": state,
                       "answered": stats["answered"] if stats else 0, "correct": stats["correct"] if stats else 0})
    cleared = sum(1 for d in detail if d["state"] == "cleared")
    percent = round(100 * cleared / len(detail)) if detail else 0
    return {"cleared": cleared, "total": len(detail), "percent": percent, "ready": percent >= threshold,
            "threshold": threshold, "sections": detail}


async def leaderboard(quiz_id: str, mode: str, team: str | None = None, limit: int = 100,
                      offset: int = 0, query: str | None = None, player_id: str | None = None) -> dict:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    rows = await db.fetch(
        "select b.player_id, b.score, b.badge, b.decade, b.passed, p.name, p.team from quiz_best_runs b"
        " join players p on p.id = b.player_id where b.quiz_id = $1 and b.mode = $2"
        " and ($3::text is null or p.team = $3) order by b.score desc, b.finished_at asc", quiz_id, mode, team)
    entries = [{"rank": i, "name": display_name(r["name"]), "team": r["team"], "score": r["score"], "badge": r["badge"],
                "decade": r["decade"], "passed": r["passed"], "you": r["player_id"] == player_id}
               for i, r in enumerate(rows, start=1)]
    my_index = next((idx for idx, e in enumerate(entries) if e["you"]), None)
    mine = dict(entries[my_index]) if my_index is not None else None
    if mine is not None and my_index is not None and my_index > 0:
        ahead = next((e for e in reversed(entries[:my_index]) if e["score"] > mine["score"]), None)
        if ahead is not None:
            mine["gapToNext"] = ahead["score"] - mine["score"]
            mine["aheadName"] = ahead["name"]
        else:
            mine["tied"] = True

    needle = (query or "").strip().lower()
    matched = (
        [e for e in entries if needle in e["name"].lower() or (e["team"] and needle in e["team"].lower())
         or (e["badge"] and needle in e["badge"].lower())]
        if needle else entries
    )
    page = matched[offset:offset + limit]

    around_you: list[dict] = []
    if not needle and my_index is not None and (my_index < offset or my_index >= offset + limit):
        start = max(0, my_index - 1)
        around_you = entries[start:my_index + 2]

    your_runs: list[dict] = []
    if player_id:
        recent = await db.fetch(
            "select id, score, badge, passed, streak_max, correct_answers, questions_answered,"
            " report_correct, report_answered, music_correct, music_answered, finished_at from runs"
            " where quiz_id = $1 and player_id = $2 and mode = $3 and status = 'completed'"
            " order by finished_at desc limit 8", quiz_id, player_id, mode)
        best_score = max((r["score"] for r in recent), default=None)
        if mine and best_score is not None:
            best_score = max(best_score, mine["score"])
        your_runs = []
        for r in recent:
            if mode == "jukebox":
                c, a = int(r["music_correct"] or 0), int(r["music_answered"] or 0)
            else:
                c, a = int(r["report_correct"] or 0), int(r["report_answered"] or 0)
            if a == 0:
                c, a = int(r["correct_answers"] or 0), int(r["questions_answered"] or 0)
            your_runs.append({
                "id": str(r["id"]),
                "score": r["score"],
                "badge": r["badge"],
                "passed": r["passed"],
                "streakMax": r["streak_max"],
                "correct": c,
                "answered": a,
                "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
                "isBest": best_score is not None and r["score"] == best_score,
            })
    teams = await db.fetch(
        "select p.team, round(avg(b.score))::int as average, count(*) as players from quiz_best_runs b"
        " join players p on p.id = b.player_id where b.quiz_id = $1 and b.mode = $2 and p.team is not null"
        " and p.team <> '' group by p.team order by average desc", quiz_id, mode)
    return {
        "mode": mode,
        "modeName": MODES[mode]["name"],
        "team": team,
        "entries": page,
        "aroundYou": around_you,
        "yourRuns": your_runs,
        "you": mine,
        "players": len(entries),
        "matched": len(matched),
        "hasMore": offset + limit < len(matched),
        "teams": teams,
    }


async def _after_party(run: dict, rules: dict, items: list[dict]) -> dict | None:
    """Music for the results screen: the game's decade, starting with the songs from its own music breaks."""
    if not (rules["music_enabled"] and rules.get("after_party", True)) or run["mode"] == "straight":
        return None
    decade = music_service.game_decade(run, rules)     # the same decade the game's soundtrack came from
    if not decade:
        return None
    heard = [item["music_track_id"] for item in items if item["music_track_id"]]
    tracks = await music_service.after_party(decade, heard, seed=run["id"])
    tracks = favorites.mix(tracks, await favorites.for_player(run["player_id"]), f"party:{run['id']}", decade)
    return {"decade": decade, "tracks": tracks} if tracks else None


async def run_summary(run: dict, rules: dict) -> dict:
    items = await db.fetch(
        "select ri.*, s.heading, q.explanation, q.source_quote, q.title as question_title,"
        " q.video_url, q.video_title from run_items ri"
        " left join doc_sections s on s.id = ri.section_id left join questions q on q.id = ri.question_id"
        " where ri.run_id = $1 and ri.answered_at is not null order by ri.position", run["id"])
    per_section: dict[str, dict] = {}
    review = []
    seen_review_qids: set[str] = set()
    results_grid: list[bool] = []
    for item in items:
        if run["mode"] != "jukebox" and item["slot"] in ("music", "jargon", "song_or_jargon", "second_chance"):
            continue
        results_grid.append(bool(item["is_correct"]))
        if item["section_id"]:
            entry = per_section.setdefault(item["section_id"], {"id": item["section_id"], "heading": item["heading"],
                                                                "answered": 0, "correct": 0})
            entry["answered"] += 1
            entry["correct"] += int(bool(item["is_correct"]))
        if not item["is_correct"]:
            qid = item.get("question_id")
            if qid and qid in seen_review_qids:
                continue
            if qid:
                seen_review_qids.add(qid)
            options = item["payload"]["options"]
            chosen = item["selected_index"]
            review.append({
                "slot": item["slot"],
                "title": item["question_title"], "text": item["payload"]["text"], "section": item["heading"],
                "correctAnswer": options[item["correct_index"]],
                "yourAnswer": options[chosen] if chosen is not None else None,
                "explanation": item["explanation"],
                "sourceQuote": item["source_quote"],
                "video": video.parse_youtube(item.get("video_url"), item.get("video_title")),
            })
    ready = await readiness(run["quiz_id"], run["player_id"], int(rules["ready_threshold"]))
    cleared_ids = {s["id"] for s in ready["sections"] if s["state"] == "cleared"}
    blind_spots = [{"id": s["id"], "heading": s["heading"]} for s in per_section.values()
                   if s["correct"] < s["answered"] and s["id"] not in cleared_ids]
    seen_blind = {b["id"] for b in blind_spots}
    for s in ready["sections"]:
        if s["state"] == "tried" and s["id"] not in seen_blind:
            blind_spots.append({"id": s["id"], "heading": s["heading"]})
            seen_blind.add(s["id"])
    untouched = [{"id": s["id"], "heading": s["heading"]} for s in ready["sections"] if s["state"] == "new"]

    show_board = bool(rules.get("leaderboard", True))
    board = await leaderboard(run["quiz_id"], run["mode"], player_id=run["player_id"]) if show_board else None
    previous_best = await db.fetchval(
        "select max(score) from runs where player_id = $1 and quiz_id = $2 and mode = $3 and status = 'completed'"
        " and id <> $4", run["player_id"], run["quiz_id"], run["mode"], run["id"])
    accuracy = round(100 * run["report_correct"] / run["report_answered"]) if run["report_answered"] else 0
    if run["mode"] == "jukebox":                    # the music side game is scored on its music
        accuracy = round(100 * run["music_correct"] / run["music_answered"]) if run["music_answered"] else 0
    unused_lifelines = (
        sum(max(0, int(v)) for v in (run["lifelines"] or {}).values())
        if run["status"] == "completed" and (
            run["mode"] == "show"
            or (run["mode"] == "sudden_death" and run["strikes"] < int(rules["sudden_death"]["strikes"]))
        )
        else 0
    )
    lifeline_bonus = unused_lifelines * 40
    return {
        "afterParty": await _after_party(run, rules, items),
        "runId": run["id"], "mode": run["mode"], "modeName": MODES[run["mode"]]["name"], "status": run["status"],
        "score": run["score"], "badge": run["badge"], "badgeInfo": badges.summary_badge_info(run, rules),
        "passed": run["passed"],
        "passMark": int(rules["straight"]["pass_mark"]) if run["mode"] == "straight" else None,
        "accuracy": accuracy, "report": {"answered": run["report_answered"], "correct": run["report_correct"]},
        "music": {"answered": run["music_answered"], "correct": run["music_correct"]},
        "streakMax": run["streak_max"], "durationSec": run["duration_sec"], "decade": run["decade"],
        "unusedLifelines": unused_lifelines, "lifelineBonus": lifeline_bonus,
        "sections": list(per_section.values()), "blindSpots": blind_spots, "untouched": untouched[:6],
        "review": review[:12], "resultsGrid": results_grid, "readiness": ready,
        "rank": board["you"]["rank"] if (board and board["you"]) else None,
        "players": board["players"] if board else 0,
        "personalBest": previous_best is None or run["score"] > previous_best, "previousBest": previous_best,
    }


# --------------------------------------------------------------------------- admin

async def quiz_overview(quiz_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select"
        " (select count(*) from questions where quiz_id = $1 and status = 'approved') as approved,"
        " (select count(*) from questions where quiz_id = $1 and status = 'approved' and kind = 'mcq') as approved_mcq,"
        " (select count(*) from questions where quiz_id = $1 and status = 'approved' and kind = 'mcq' and origin = 'manual') as approved_manual_mcq,"
        " (select count(*) from questions where quiz_id = $1 and status = 'approved' and kind = 'mcq' and origin = 'generated') as approved_ai_mcq,"
        " (select count(*) from questions where quiz_id = $1 and status = 'pending') as pending,"
        " (select count(*) from questions where quiz_id = $1 and origin = 'manual') as manual,"
        " (select count(*) from questions where quiz_id = $1 and flag_count > 0 and status <> 'rejected') as flagged,"
        " (select count(distinct player_id) from runs where quiz_id = $1) as players,"
        " (select count(*) from runs where quiz_id = $1 and status = 'completed') as games,"
        " (select count(*) from live_sessions where quiz_id = $1) as live_sessions,"
        " (select count(*) from live_sessions where quiz_id = $1 and status <> 'finished') as active_live_sessions,"
        " (select count(*) from documents where quiz_id = $1) as documents", quiz_id)
    return dict(row)


async def quiz_overviews(quiz_ids: list[str]) -> dict[str, dict[str, int]]:
    """Batch overview counts for every quiz in 4 queries instead of 11 × N."""
    if not quiz_ids:
        return {}
    empty = {
        "approved": 0, "approved_mcq": 0, "approved_manual_mcq": 0, "approved_ai_mcq": 0,
        "pending": 0, "manual": 0, "flagged": 0, "players": 0, "games": 0,
        "live_sessions": 0, "active_live_sessions": 0, "documents": 0,
    }
    out = {qid: dict(empty) for qid in quiz_ids}
    q_rows = await db.fetch(
        "select quiz_id,"
        " count(*) filter (where status = 'approved') as approved,"
        " count(*) filter (where status = 'approved' and kind = 'mcq') as approved_mcq,"
        " count(*) filter (where status = 'approved' and kind = 'mcq' and origin = 'manual') as approved_manual_mcq,"
        " count(*) filter (where status = 'approved' and kind = 'mcq' and origin = 'generated') as approved_ai_mcq,"
        " count(*) filter (where status = 'pending') as pending,"
        " count(*) filter (where origin = 'manual') as manual,"
        " count(*) filter (where flag_count > 0 and status <> 'rejected') as flagged"
        " from questions where quiz_id = any($1::uuid[]) group by quiz_id", quiz_ids)
    for r in q_rows:
        out[r["quiz_id"]].update({
            "approved": r["approved"], "approved_mcq": r["approved_mcq"],
            "approved_manual_mcq": r["approved_manual_mcq"], "approved_ai_mcq": r["approved_ai_mcq"],
            "pending": r["pending"], "manual": r["manual"], "flagged": r["flagged"],
        })
    r_rows = await db.fetch(
        "select quiz_id, count(distinct player_id) as players,"
        " count(*) filter (where status = 'completed') as games"
        " from runs where quiz_id = any($1::uuid[]) group by quiz_id", quiz_ids)
    for r in r_rows:
        out[r["quiz_id"]].update({"players": r["players"], "games": r["games"]})
    l_rows = await db.fetch(
        "select quiz_id, count(*) as live_sessions,"
        " count(*) filter (where status <> 'finished') as active_live_sessions"
        " from live_sessions where quiz_id = any($1::uuid[]) group by quiz_id",
        quiz_ids)
    for r in l_rows:
        out[r["quiz_id"]].update({
            "live_sessions": r["live_sessions"],
            "active_live_sessions": r["active_live_sessions"],
        })
    d_rows = await db.fetch(
        "select quiz_id, count(*) as documents from documents where quiz_id = any($1::uuid[]) group by quiz_id",
        quiz_ids)
    for r in d_rows:
        out[r["quiz_id"]]["documents"] = r["documents"]
    return out


async def participants(quiz: dict, rules: dict) -> list[dict]:
    askable = {s["id"] for s in await askable_sections(quiz["id"])}
    sections_total = len(askable)
    threshold = int(rules["ready_threshold"])
    cleared_by_player: dict[str, int] = {}
    for row in await db.fetch(_SECTION_ANSWERS.format(player="", recent=RECENT), quiz["id"]):
        if row["section_id"] in askable and _cleared(row):
            cleared_by_player[row["player_id"]] = cleared_by_player.get(row["player_id"], 0) + 1
    rows = await db.fetch(
        "select p.id, p.name, p.email, p.team, s.report_answered, s.report_correct, s.unique_questions,"
        " s.sections_cleared, s.sections_seen, s.games, s.peeks_used, s.last_played_at,"
        " (select max(score) from runs r where r.player_id = p.id and r.quiz_id = $1 and r.status = 'completed') as best_score,"
        " (select bool_or(passed) from runs r where r.player_id = p.id and r.quiz_id = $1 and r.mode = 'straight') as passed"
        " from player_quiz_stats s join players p on p.id = s.player_id where s.quiz_id = $1"
        " order by s.report_correct desc", quiz["id"])
    result = []
    for r in rows:
        r = {**r, "sections_cleared": cleared_by_player.get(r["id"], 0)}   # the same rule as the player's own screen
        percent = round(100 * r["sections_cleared"] / sections_total) if sections_total else 0
        result.append({
            "id": r["id"], "name": r["name"], "email": r["email"], "team": r["team"], "games": r["games"],
            "bestScore": r["best_score"], "answered": r["report_answered"], "correct": r["report_correct"],
            "accuracy": round(100 * r["report_correct"] / r["report_answered"]) if r["report_answered"] else None,
            "sectionsCleared": r["sections_cleared"], "sectionsTotal": sections_total, "readiness": percent,
            "ready": percent >= threshold, "peeksUsed": r["peeks_used"], "passed": r["passed"],
            "lastPlayedAt": r["last_played_at"],
        })
    result.sort(key=lambda p: (-p["sectionsCleared"], -(p["correct"] or 0)))
    return result


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection (CWE-1236) on user-supplied CSV cells."""
    if isinstance(value, str) and value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


async def participants_csv(quiz: dict, rules: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Name", "Email", "Team", "Ready", "Readiness %", "Sections cleared", "Sections total",
                     "Report questions answered", "Correct", "Accuracy %", "Games", "Best score", "Peeks used",
                     "Last played (UTC)"])
    for p in await participants(quiz, rules):
        writer.writerow([
            _csv_cell(p["name"]), _csv_cell(p["email"]), _csv_cell(p["team"] or ""),
            "yes" if p["ready"] else "no", p["readiness"],
            p["sectionsCleared"], p["sectionsTotal"], p["answered"], p["correct"],
            p["accuracy"] if p["accuracy"] is not None else "",
            p["games"], p["bestScore"] if p["bestScore"] is not None else "", p["peeksUsed"],
            p["lastPlayedAt"].strftime("%Y-%m-%d %H:%M") if p["lastPlayedAt"] else "",
        ])
    return out.getvalue()


async def insights(quiz_id: str) -> dict:
    """Which parts of the report did not land, and which questions need a look."""
    sections = await db.fetch(
        "select s.id, s.heading, coalesce(st.answered, 0) as answered, coalesce(st.correct, 0) as correct,"
        " coalesce(st.players, 0) as players from doc_sections s join documents d on d.id = s.document_id"
        " left join section_stats st on st.section_id = s.id where s.quiz_id = $1 order by d.created_at, s.ordinal",
        quiz_id)
    for section in sections:
        section["accuracy"] = round(100 * section["correct"] / section["answered"]) if section["answered"] else None
    hardest = await db.fetch(
        "select q.id, q.title, q.question_text, q.kind, q.times_served, q.times_correct, q.flag_count, s.heading,"
        " round(100.0 * q.times_correct / q.times_served)::int as accuracy from questions q"
        " left join doc_sections s on s.id = q.section_id where q.quiz_id = $1 and q.times_served >= 3"
        " and q.status = 'approved' order by (q.times_correct::float / q.times_served), q.times_served desc limit 8",
        quiz_id)
    weakest = sorted([s for s in sections if s["accuracy"] is not None], key=lambda s: s["accuracy"])[:3]
    return {"sections": sections, "hardestQuestions": hardest, "weakestSections": weakest}


def worse_than_guessing(correct: int, served: int, choices: int) -> bool:
    """True when we can be confident (95%) that players do worse on a question than blind guessing would.

    That is the mark of a wrong answer key: people who know the material pick the truly right
    answer and are told it is wrong. A hard question is not the same thing – plenty of honest
    questions are answered correctly only a third of the time – so a few bad results are not enough.
    """
    if served < 12:
        return False
    z = 1.96
    rate = correct / served
    centre = rate + z * z / (2 * served)
    margin = z * math.sqrt(rate * (1 - rate) / served + z * z / (4 * served * served))
    upper = (centre + margin) / (1 + z * z / served)          # top of the Wilson interval for the true rate
    return upper < 0.9 / max(2, choices)


async def note_question_health(question_id: str) -> None:
    """Pull a question for review when players flag it twice, or when they do worse on it than guessing."""
    question = await db.fetchrow(
        "select quiz_id, kind, status, flag_count, times_served, times_correct from questions where id = $1", question_id)
    if question is None or question["status"] != "approved":
        return
    flagged = question["flag_count"] >= 2
    brutal = worse_than_guessing(question["times_correct"], question["times_served"], 4 if question["kind"] == "mcq" else 2)
    if flagged or brutal:
        reason = "flagged by players" if flagged else "almost nobody answers it correctly"
        await db.execute(
            "update questions set status = 'pending', quality = quality || $2::jsonb, updated_at = now() where id = $1",
            question_id, {"pulled": reason})
        bank.invalidate(question["quiz_id"])
