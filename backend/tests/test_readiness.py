"""Readiness: a section counts as cleared only while the player knows it."""
from __future__ import annotations

from services import stats


def row(answered: int, correct: int, choice: int | None = None) -> dict:
    # choice: how many of the recent answers were multiple-choice (not Fact or Fake); all of them by default
    return {"recent_answered": answered, "recent_correct": correct,
            "recent_choice_correct": correct if choice is None else choice}


def test_one_lucky_guess_no_longer_clears_a_section():
    # It used to take one right answer ever, in any game: a 12% player was "ready" with 8/8 (user, 2026-11-21).
    assert not stats._cleared(None)
    assert not stats._cleared(row(2, 1)) and not stats._cleared(row(4, 2)) and not stats._cleared(row(3, 2))
    assert not stats._cleared(row(1, 1, choice=0))       # a single Fact or Fake call is a coin toss


def test_knowing_it_now_clears_it():
    assert stats._cleared(row(2, 2)) and stats._cleared(row(3, 3)) and stats._cleared(row(4, 3))
    assert stats._cleared(row(1, 1))                     # one right multiple-choice answer (a Straight Quiz covers each once)
    assert not stats._cleared(row(1, 0))
