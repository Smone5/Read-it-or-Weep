"""Reading the question bank and choosing what to ask next.

Choice is what makes no two games the same and what makes replays useful:
questions the player has never seen come first, then ones they previously got
wrong, then the rest; within that, sections not yet covered in this game are
preferred, pinned "must-ask" questions jump the queue, and a random nudge breaks
ties so two colleagues starting at the same moment still get different games.

One more rule keeps a single game from repeating itself: a question whose answer is
essentially the answer to one already asked in this game (the same fact, worded
differently) is held back until nothing else is left.
"""
from __future__ import annotations

import random
import re
import time
from collections import Counter
from typing import Iterable

import db

_BANK_COLUMNS = ("id, quiz_id, section_id, chunk_id, image_id, kind, source_type, skill, title, question_text, options,"
                 " correct_index, is_true, explanation, source_quote, difficulty, origin, pinned, times_served")
_cache: dict[str, tuple[float, list[dict]]] = {}
_TTL = 20.0


def invalidate(quiz_id: str) -> None:
    _cache.pop(quiz_id, None)


async def approved(quiz_id: str, sources: str = "all") -> list[dict]:
    cached = _cache.get(quiz_id)
    if cached is None or time.monotonic() - cached[0] > _TTL:
        rows = await db.fetch(
            f"select {_BANK_COLUMNS} from questions where quiz_id = $1 and status = 'approved'", quiz_id)
        _cache[quiz_id] = (time.monotonic(), rows)
        cached = _cache[quiz_id]
    rows = cached[1]
    if sources == "manual":
        return [r for r in rows if r["origin"] == "manual"]
    if sources == "ai":
        return [r for r in rows if r["origin"] == "generated"]
    return rows


async def sections(quiz_id: str) -> list[dict]:
    return await db.fetch(
        "select s.id, s.heading, s.ordinal, s.document_id from doc_sections s"
        " join documents d on d.id = s.document_id where s.quiz_id = $1 order by d.created_at, s.ordinal", quiz_id)


async def history(player_id: str, quiz_id: str) -> dict[str, dict]:
    """question_id -> {'times': n, 'correct': ever answered correctly} for this player."""
    rows = await db.fetch(
        "select question_id, count(*) as times, bool_or(coalesce(is_correct, false)) as correct"
        " from run_items where player_id = $1 and quiz_id = $2 and question_id is not null"
        " and answered_at is not null group by question_id", player_id, quiz_id)
    return {r["question_id"]: {"times": r["times"], "correct": r["correct"]} for r in rows}


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}

_FILLER = {"the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "its", "their", "than", "into",
           "over", "per", "has", "had", "have", "not", "but", "more", "most", "some", "any", "about", "both", "whether",
           "which", "what", "when", "will", "would", "been", "being", "after", "before", "during", "meridian"}


def key_terms(question: dict) -> frozenset[str]:
    """The words that carry a question's answer: numbers whole, other words cut to a short stem."""
    cached = question.get("_terms")
    if cached is not None:
        return cached
    if question["kind"] == "mcq" and question.get("options") and question.get("correct_index") is not None:
        text = question["options"][question["correct_index"]]
    else:
        text = question["question_text"]          # a Fact-or-Fake statement is its own answer
    terms = set()
    for raw in re.findall(r"[a-z]+|\d+(?:[.,]\d+)*", text.lower()):
        if raw[0].isdigit():
            terms.add(raw.replace(",", "").rstrip("."))
        elif len(raw) > 2 and raw not in _FILLER:
            terms.add(raw[:5])
    question["_terms"] = frozenset(terms)
    return question["_terms"]


def same_fact(a: frozenset[str], b: frozenset[str]) -> bool:
    """True when two answers are, near enough, the same answer."""
    if not a or not b:
        return False
    shared = len(a & b)
    return shared / min(len(a), len(b)) >= 0.75 and shared / max(len(a), len(b)) >= 0.5


def choose(candidates: Iterable[dict], seen: dict[str, dict], used: set[str], *,
           section_load: Counter | None = None, difficulty: str | None = None,
           section_id: str | None = None, asked: list[frozenset[str]] | None = None) -> dict | None:
    """Pick the best next question. ``asked`` holds the key terms of what this game has already asked."""
    section_load = section_load or Counter()
    best: tuple[float, dict] | None = None
    for question in candidates:
        if question["id"] in used:
            continue
        if section_id is not None and question["section_id"] != section_id:
            continue
        past = seen.get(question["id"])
        tier = 0 if past is None else (1 if not past["correct"] else 2)
        if question["pinned"] and (past is None or not past["correct"]):
            tier = -1
        cost = tier * 10.0 + section_load[question["section_id"]] * 1.2 + random.random() * 0.9
        if difficulty:
            cost += abs(_DIFFICULTY_ORDER[question["difficulty"]] - _DIFFICULTY_ORDER[difficulty]) * 0.7
        if asked and any(same_fact(key_terms(question), terms) for terms in asked):
            cost += 6.0                             # the same fact again: only if nothing else in this tier is left
        if best is None or cost < best[0]:
            best = (cost, question)
    return best[1] if best else None


def counts(bank: list[dict]) -> dict[str, int]:
    return {
        "mcq": sum(1 for q in bank if q["kind"] == "mcq"),
        "picture": sum(1 for q in bank if q["kind"] == "mcq" and q["source_type"] == "image"),
        "text": sum(1 for q in bank if q["kind"] == "mcq" and q["source_type"] != "image"),
        "fact_fake": sum(1 for q in bank if q["kind"] == "fact_fake"),
    }
