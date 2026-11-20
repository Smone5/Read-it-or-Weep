"""Results: leaderboards, readiness, end-of-game summaries and admin insight."""
from __future__ import annotations

import csv
import io
import random
from typing import Any

import db
from services import bank, music_service
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


async def readiness(quiz_id: str, player_id: str, threshold: int) -> dict:
    sections = await askable_sections(quiz_id)
    rows = await db.fetch(
        "select section_id, count(*) filter (where answered_at is not null) as answered,"
        " count(*) filter (where is_correct) as correct from run_items"
        " where quiz_id = $1 and player_id = $2 and section_id is not null and slot <> 'music' group by section_id",
        quiz_id, player_id)
    by_section = {r["section_id"]: r for r in rows}
    detail = []
    for section in sections:
        stats = by_section.get(section["id"])
        state = "cleared" if stats and stats["correct"] else ("tried" if stats and stats["answered"] else "new")
        detail.append({"id": section["id"], "heading": section["heading"], "state": state,
                       "answered": stats["answered"] if stats else 0, "correct": stats["correct"] if stats else 0})
    cleared = sum(1 for d in detail if d["state"] == "cleared")
    percent = round(100 * cleared / len(detail)) if detail else 0
    return {"cleared": cleared, "total": len(detail), "percent": percent, "ready": percent >= threshold,
            "threshold": threshold, "sections": detail}


async def leaderboard(quiz_id: str, mode: str, team: str | None = None, limit: int = 20,
                      player_id: str | None = None) -> dict:
    rows = await db.fetch(
        "select b.player_id, b.score, b.badge, b.decade, b.passed, p.name, p.team from quiz_best_runs b"
        " join players p on p.id = b.player_id where b.quiz_id = $1 and b.mode = $2"
        " and ($3::text is null or p.team = $3) order by b.score desc, b.finished_at asc", quiz_id, mode, team)
    entries = [{"rank": i, "name": display_name(r["name"]), "team": r["team"], "score": r["score"], "badge": r["badge"],
                "decade": r["decade"], "passed": r["passed"], "you": r["player_id"] == player_id}
               for i, r in enumerate(rows, start=1)]
    mine = next((e for e in entries if e["you"]), None)
    teams = await db.fetch(
        "select p.team, round(avg(b.score))::int as average, count(*) as players from quiz_best_runs b"
        " join players p on p.id = b.player_id where b.quiz_id = $1 and b.mode = $2 and p.team is not null"
        " and p.team <> '' group by p.team order by average desc", quiz_id, mode)
    return {"mode": mode, "modeName": MODES[mode]["name"], "entries": entries[:limit], "you": mine,
            "players": len(entries), "teams": teams}


async def _after_party(run: dict, rules: dict, items: list[dict]) -> dict | None:
    """Music for the results screen: the game's decade, starting with the songs from its own music breaks."""
    if not (rules["music_enabled"] and rules.get("after_party", True)) or run["mode"] == "straight":
        return None
    decade = run["decade"] or (random.choice(rules["decades"]) if rules["decades"] else None)
    if not decade:
        return None
    heard = [item["music_track_id"] for item in items if item["music_track_id"]]
    tracks = await music_service.after_party(decade, heard, seed=run["id"])
    return {"decade": decade, "tracks": tracks} if tracks else None


async def run_summary(run: dict, rules: dict) -> dict:
    items = await db.fetch(
        "select ri.*, s.heading, q.explanation, q.title as question_title from run_items ri"
        " left join doc_sections s on s.id = ri.section_id left join questions q on q.id = ri.question_id"
        " where ri.run_id = $1 and ri.answered_at is not null order by ri.position", run["id"])
    per_section: dict[str, dict] = {}
    review = []
    for item in items:
        if item["slot"] == "music":
            continue
        if item["section_id"]:
            entry = per_section.setdefault(item["section_id"], {"id": item["section_id"], "heading": item["heading"],
                                                                "answered": 0, "correct": 0})
            entry["answered"] += 1
            entry["correct"] += int(bool(item["is_correct"]))
        if not item["is_correct"]:
            options = item["payload"]["options"]
            chosen = item["selected_index"]
            review.append({
                "title": item["question_title"], "text": item["payload"]["text"], "section": item["heading"],
                "correctAnswer": options[item["correct_index"]],
                "yourAnswer": options[chosen] if chosen is not None else None,
                "explanation": item["explanation"],
            })
    ready = await readiness(run["quiz_id"], run["player_id"], int(rules["ready_threshold"]))
    cleared_ids = {s["id"] for s in ready["sections"] if s["state"] == "cleared"}
    blind_spots = [{"id": s["id"], "heading": s["heading"]} for s in per_section.values()
                   if s["correct"] < s["answered"] and s["id"] not in cleared_ids]
    untouched = [{"id": s["id"], "heading": s["heading"]} for s in ready["sections"] if s["state"] == "new"]

    board = await leaderboard(run["quiz_id"], run["mode"], player_id=run["player_id"])
    previous_best = await db.fetchval(
        "select max(score) from runs where player_id = $1 and quiz_id = $2 and mode = $3 and status = 'completed'"
        " and id <> $4", run["player_id"], run["quiz_id"], run["mode"], run["id"])
    accuracy = round(100 * run["report_correct"] / run["report_answered"]) if run["report_answered"] else 0
    return {
        "afterParty": await _after_party(run, rules, items),
        "runId": run["id"], "mode": run["mode"], "modeName": MODES[run["mode"]]["name"], "status": run["status"],
        "score": run["score"], "badge": run["badge"], "passed": run["passed"],
        "passMark": int(rules["straight"]["pass_mark"]) if run["mode"] == "straight" else None,
        "accuracy": accuracy, "report": {"answered": run["report_answered"], "correct": run["report_correct"]},
        "music": {"answered": run["music_answered"], "correct": run["music_correct"]},
        "streakMax": run["streak_max"], "durationSec": run["duration_sec"], "decade": run["decade"],
        "sections": list(per_section.values()), "blindSpots": blind_spots, "untouched": untouched[:6],
        "review": review[:12], "readiness": ready,
        "rank": board["you"]["rank"] if board["you"] else None, "players": board["players"],
        "personalBest": previous_best is None or run["score"] > previous_best, "previousBest": previous_best,
    }


# --------------------------------------------------------------------------- admin

async def quiz_overview(quiz_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select"
        " (select count(*) from questions where quiz_id = $1 and status = 'approved') as approved,"
        " (select count(*) from questions where quiz_id = $1 and status = 'pending') as pending,"
        " (select count(*) from questions where quiz_id = $1 and origin = 'manual') as manual,"
        " (select count(*) from questions where quiz_id = $1 and flag_count > 0 and status <> 'rejected') as flagged,"
        " (select count(distinct player_id) from runs where quiz_id = $1) as players,"
        " (select count(*) from runs where quiz_id = $1 and status = 'completed') as games,"
        " (select count(*) from documents where quiz_id = $1) as documents", quiz_id)
    return dict(row)


async def participants(quiz: dict, rules: dict) -> list[dict]:
    sections_total = len(await askable_sections(quiz["id"]))
    threshold = int(rules["ready_threshold"])
    rows = await db.fetch(
        "select p.id, p.name, p.email, p.team, s.report_answered, s.report_correct, s.unique_questions,"
        " s.sections_cleared, s.sections_seen, s.games, s.peeks_used, s.last_played_at,"
        " (select max(score) from runs r where r.player_id = p.id and r.quiz_id = $1 and r.status = 'completed') as best_score,"
        " (select bool_or(passed) from runs r where r.player_id = p.id and r.quiz_id = $1 and r.mode = 'straight') as passed"
        " from player_quiz_stats s join players p on p.id = s.player_id where s.quiz_id = $1"
        " order by s.sections_cleared desc, s.report_correct desc", quiz["id"])
    result = []
    for r in rows:
        percent = round(100 * r["sections_cleared"] / sections_total) if sections_total else 0
        result.append({
            "id": r["id"], "name": r["name"], "email": r["email"], "team": r["team"], "games": r["games"],
            "bestScore": r["best_score"], "answered": r["report_answered"], "correct": r["report_correct"],
            "accuracy": round(100 * r["report_correct"] / r["report_answered"]) if r["report_answered"] else 0,
            "sectionsCleared": r["sections_cleared"], "sectionsTotal": sections_total, "readiness": percent,
            "ready": percent >= threshold, "peeksUsed": r["peeks_used"], "passed": r["passed"],
            "lastPlayedAt": r["last_played_at"],
        })
    return result


async def participants_csv(quiz: dict, rules: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Name", "Email", "Team", "Ready", "Readiness %", "Sections cleared", "Sections total",
                     "Report questions answered", "Correct", "Accuracy %", "Games", "Best score", "Peeks used",
                     "Last played (UTC)"])
    for p in await participants(quiz, rules):
        writer.writerow([p["name"], p["email"], p["team"] or "", "yes" if p["ready"] else "no", p["readiness"],
                         p["sectionsCleared"], p["sectionsTotal"], p["answered"], p["correct"], p["accuracy"],
                         p["games"], p["bestScore"] if p["bestScore"] is not None else "", p["peeksUsed"],
                         p["lastPlayedAt"].strftime("%Y-%m-%d %H:%M") if p["lastPlayedAt"] else ""])
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


async def note_question_health(question_id: str) -> None:
    """Pull a question for review when players flag it twice, or when almost nobody gets it right."""
    question = await db.fetchrow(
        "select quiz_id, status, flag_count, times_served, times_correct from questions where id = $1", question_id)
    if question is None or question["status"] != "approved":
        return
    flagged = question["flag_count"] >= 2
    brutal = question["times_served"] >= 8 and question["times_correct"] / question["times_served"] < 0.15
    if flagged or brutal:
        reason = "flagged by players" if flagged else "almost nobody answers it correctly"
        await db.execute(
            "update questions set status = 'pending', quality = quality || $2::jsonb, updated_at = now() where id = $1",
            question_id, {"pulled": reason})
        bank.invalidate(question["quiz_id"])
