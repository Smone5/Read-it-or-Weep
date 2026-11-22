"""End-of-game ratings (badges) across every play mode.

Each mode evaluates mastery differently:
  * Show / Straight   — pure report accuracy across 6 crisp tiers, with a dedicated
                        100% flawless crown ("Cover to Cover") and a fair middle tier
                        ("Got the Gist", 60-74%) so 68% isn't lumped in with 40% skimmers.
  * Sudden Death      — survival-aware: because striking out always ends with 3 misses,
                        pure accuracy caps at correct/(correct+3). Tiers reward how many
                        report questions you survived (or clearing the bank without 3 strikes).
  * Speed Run         — volume + accuracy: prevents stalling at 2/2 (100%) from earning
                        the top title while rewarding fast, accurate runs against the clock.
  * Office Jukebox    — music accuracy across 5 office-music titles.
  * Abandoned runs    — quitting before 3 answers earns "Warm-Up Only" instead of a rank.
"""
from __future__ import annotations

from typing import Any

# Standard accuracy-based report tiers (Show / Straight).
# (floor_pct, label, tier, tone, icon, description)
REPORT_TIERS: tuple[tuple[int, str, int, str, str, str], ...] = (
    (100, "Cover to Cover",       6, "legend", "crown",  "Flawless run — you didn't miss a single detail."),
    (90,  "Subject Expert",       5, "gold",   "trophy", "Mastered the material from top to bottom."),
    (75,  "Well Read",            4, "good",   "star",   "Solid grasp — you clearly did the reading."),
    (60,  "Got the Gist",         3, "brand",  "book",   "Caught the big ideas, missed some fine print."),
    (40,  "Skimmer",              2, "warn",   "bolt",   "Glanced at the headings and guessed the rest."),
    (0,   "Judged It By Its Cover", 1, "bad",  "bulb",   "Looks like you opened this for the first time right now."),
)

# Backwards-compatible (floor, name) view.
REPORT_BADGES: list[tuple[int, str]] = [(floor, label) for floor, label, *_ in REPORT_TIERS]

# Sudden Death survival tiers by report_correct count.
SUDDEN_DEATH_TIERS: tuple[tuple[int, str, int, str, str, str], ...] = (
    (20, "Last One Standing",      6, "legend", "crown",  "Outlasted the deck — nerves of steel."),
    (12, "Ironclad",               5, "gold",   "trophy", "Deep survival run under mounting pressure."),
    (7,  "Well Read",              4, "good",   "star",   "Held your ground well into the report."),
    (4,  "Got the Gist",           3, "brand",  "book",   "Cleared the warm-up before the strikes caught up."),
    (2,  "Skimmer",                2, "warn",   "bolt",   "Survived a couple rounds, then hit rough water."),
    (0,  "Judged It By Its Cover", 1, "bad",    "bulb",   "Three strikes in a hurry — time to open the report."),
)

# Speed Run tiers (base thresholds for a 90s clock, scaled proportionally if clock_sec differs).
# (base_correct_at_90s, min_accuracy_pct, label, tier, tone, icon, description)
SPEED_TIERS: tuple[tuple[int, int, str, int, str, str, str], ...] = (
    (16, 85, "Speed Demon",            6, "legend", "crown",  "Blistering pace and razor-sharp accuracy."),
    (12, 75, "Rapid Reader",           5, "gold",   "trophy", "Fast hands and real command of the report."),
    (8,  65, "Quick Study",            4, "good",   "star",   "Strong rhythm against the clock."),
    (5,  50, "Got the Gist",           3, "brand",  "book",   "Hit the headlines while racing the timer."),
    (3,  40, "Skimmer",                2, "warn",   "bolt",   "More speed than certainty — give it another skim."),
    (0,  0,  "Judged It By Its Cover", 1, "bad",    "bulb",   "The clock won this round — read up and run it back."),
)

# Office Jukebox music-accuracy tiers.
JUKEBOX_TIERS: tuple[tuple[int, str, int, str, str, str], ...] = (
    (90, "Chief Music Officer",     5, "legend", "crown",  "Golden ears — you basically run the soundtrack."),
    (75, "Senior Playlist Manager", 4, "gold",   "disc",   "Serious music instincts across the decades."),
    (60, "VP of Vibes",             3, "good",   "star",   "Good ear — you knew more than you thought."),
    (40, "Jingle Intern",           2, "warn",   "bolt",   "Caught a few hooks, hummed through the rest."),
    (0,  "Hold Music Specialist",   1, "bad",    "bulb",   "Every DJ starts somewhere — give it another spin."),
)

WARMUP_ONLY: dict[str, Any] = {
    "label": "Warm-Up Only",
    "tier": 0,
    "tone": "muted",
    "icon": "flag",
    "description": "Ended early — answer at least 3 questions to earn a rank.",
    "nextLabel": None,
    "nextHint": "Finish a run to unlock your report rating.",
}

# Tone/icon lookup for any historical badge string already stored in runs.badge.
_LEGACY_META: dict[str, tuple[int, str, str, str]] = {
    "Briefing Boss": (5, "gold", "trophy", "Mastered the material from top to bottom."),
}
for _tiers in (REPORT_TIERS, SUDDEN_DEATH_TIERS, JUKEBOX_TIERS):
    for _, _label, _tier, _tone, _icon, _desc in _tiers:
        _LEGACY_META.setdefault(_label, (_tier, _tone, _icon, _desc))
for _, _, _label, _tier, _tone, _icon, _desc in SPEED_TIERS:
    _LEGACY_META.setdefault(_label, (_tier, _tone, _icon, _desc))


def _plural_answers(n: int) -> str:
    return "1 more right answer" if n == 1 else f"{n} more right answers"


def _accuracy_needed(correct: int, answered: int, target_pct: int) -> int:
    """Minimum additional right answers out of the same question count to reach target_pct."""
    if answered <= 0:
        return 1
    if target_pct >= 100:
        return max(1, answered - correct)
    for k in range(correct + 1, answered + 1):
        if round(100 * k / answered) >= target_pct:
            return k - correct
    return max(1, answered - correct)


def evaluate_report_accuracy(correct: int, answered: int) -> dict[str, Any]:
    """6-tier accuracy rating for Show and Straight modes."""
    accuracy = round(100 * correct / answered) if answered else 0
    flawless = answered >= 3 and correct == answered
    chosen_idx = len(REPORT_TIERS) - 1
    for idx, (floor, *_rest) in enumerate(REPORT_TIERS):
        if floor == 100:
            if flawless:
                chosen_idx = idx
                break
        elif accuracy >= floor:
            chosen_idx = idx
            break

    _, label, tier, tone, icon, desc = REPORT_TIERS[chosen_idx]
    next_label: str | None = None
    next_hint: str | None = None
    if chosen_idx == 0:
        next_hint = "Top rank achieved!"
    else:
        next_floor, next_label, *_ = REPORT_TIERS[chosen_idx - 1]
        if next_floor == 100:
            miss = max(1, answered - correct)
            next_hint = (f"1 slip away from {next_label}" if miss == 1
                         else f"Go flawless next run for {next_label}")
        else:
            needed = _accuracy_needed(correct, answered, next_floor)
            next_hint = f"{_plural_answers(needed)} (or {next_floor}%+) for {next_label}"
    return {
        "label": label, "tier": tier, "tone": tone, "icon": icon,
        "description": desc, "nextLabel": next_label, "nextHint": next_hint,
    }


def evaluate_sudden_death(
    correct: int,
    answered: int,
    strikes: int,
    max_strikes: int,
    *,
    cleared_bank: bool | None = None,
) -> dict[str, Any]:
    """Survival-based rating for Sudden Death (avoids the 3-strike accuracy ceiling)."""
    if cleared_bank is None:
        cleared = answered >= 10 and strikes < max_strikes and correct >= 10
    else:
        cleared = bool(cleared_bank) and strikes < max_strikes and answered >= 3
    chosen_idx = len(SUDDEN_DEATH_TIERS) - 1
    if cleared:
        chosen_idx = 0
    else:
        for idx, (floor_correct, *_rest) in enumerate(SUDDEN_DEATH_TIERS):
            if correct >= floor_correct:
                chosen_idx = idx
                break

    _, label, tier, tone, icon, desc = SUDDEN_DEATH_TIERS[chosen_idx]
    next_label: str | None = None
    next_hint: str | None = None
    if chosen_idx == 0:
        next_hint = "Top survival rank achieved!"
    else:
        next_floor, next_label, *_ = SUDDEN_DEATH_TIERS[chosen_idx - 1]
        needed = max(1, next_floor - correct)
        next_hint = f"{_plural_answers(needed)} for {next_label}"
    return {
        "label": label, "tier": tier, "tone": tone, "icon": icon,
        "description": desc, "nextLabel": next_label, "nextHint": next_hint,
    }


def evaluate_speed(correct: int, answered: int, clock_sec: int = 90) -> dict[str, Any]:
    """Volume + accuracy rating for Speed Run so stalling at 2/2 can't cheese top rank."""
    accuracy = round(100 * correct / answered) if answered else 0
    scale = max(0.5, clock_sec / 90.0)
    targets = [(max(2, round(base * scale)) if base > 0 else 0, min_acc) for base, min_acc, *_ in SPEED_TIERS]

    chosen_idx = len(SPEED_TIERS) - 1
    for idx, (req_correct, req_acc) in enumerate(targets):
        # For the Skimmer band, hitting either volume OR >=40% accuracy qualifies; higher tiers require both.
        if SPEED_TIERS[idx][2] == "Skimmer":
            if (correct >= req_correct and answered > 0) or (answered >= 2 and accuracy >= req_acc):
                chosen_idx = idx
                break
        elif correct >= req_correct and accuracy >= req_acc:
            chosen_idx = idx
            break

    _, _, label, tier, tone, icon, desc = SPEED_TIERS[chosen_idx]
    next_label: str | None = None
    next_hint: str | None = None
    if chosen_idx == 0:
        next_hint = "Top speed rank achieved!"
    else:
        req_correct, req_acc = targets[chosen_idx - 1]
        next_label = SPEED_TIERS[chosen_idx - 1][2]
        short = max(0, req_correct - correct)
        if short > 0 and accuracy < req_acc:
            next_hint = f"{_plural_answers(short)} and {req_acc}%+ accuracy for {next_label}"
        elif short > 0:
            next_hint = f"{_plural_answers(short)} ({req_correct}+ total) for {next_label}"
        else:
            next_hint = f"Hit {req_acc}%+ accuracy for {next_label}"
    return {
        "label": label, "tier": tier, "tone": tone, "icon": icon,
        "description": desc, "nextLabel": next_label, "nextHint": next_hint,
    }


def evaluate_jukebox(music_correct: int, music_answered: int) -> dict[str, Any]:
    """5-tier music accuracy rating for Office Jukebox."""
    share = round(100 * music_correct / music_answered) if music_answered else 0
    chosen_idx = len(JUKEBOX_TIERS) - 1
    for idx, (floor, *_rest) in enumerate(JUKEBOX_TIERS):
        if share >= floor:
            chosen_idx = idx
            break
    _, label, tier, tone, icon, desc = JUKEBOX_TIERS[chosen_idx]
    next_label: str | None = None
    next_hint: str | None = None
    if chosen_idx == 0:
        next_hint = "Top DJ rank achieved!"
    else:
        next_floor, next_label, *_ = JUKEBOX_TIERS[chosen_idx - 1]
        needed = _accuracy_needed(music_correct, music_answered, next_floor)
        next_hint = f"{_plural_answers(needed)} ({next_floor}%+) for {next_label}"
    return {
        "label": label, "tier": tier, "tone": tone, "icon": icon,
        "description": desc, "nextLabel": next_label, "nextHint": next_hint,
    }


def evaluate_run(
    run: dict[str, Any],
    rules: dict[str, Any],
    *,
    status: str | None = None,
    cleared_bank: bool | None = None,
) -> dict[str, Any]:
    """Compute the full rating metadata for a finished (or finishing) run."""
    run_status = status or run.get("status") or "completed"
    if run_status != "completed":
        return dict(WARMUP_ONLY)
    mode = run.get("mode", "show")
    if mode == "jukebox":
        return evaluate_jukebox(int(run.get("music_correct") or 0), int(run.get("music_answered") or 0))
    correct = int(run.get("report_correct") or 0)
    answered = int(run.get("report_answered") or 0)
    if mode == "sudden_death":
        max_strikes = int((rules.get("sudden_death") or {}).get("strikes", 3))
        return evaluate_sudden_death(
            correct, answered, int(run.get("strikes") or 0), max_strikes, cleared_bank=cleared_bank,
        )
    if mode == "speed":
        clock_sec = int((rules.get("speed") or {}).get("clock_sec", 90))
        return evaluate_speed(correct, answered, clock_sec)
    return evaluate_report_accuracy(correct, answered)


def summary_badge_info(run: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Return badgeInfo consistent with the stored badge (gracefully handling legacy rows)."""
    info = evaluate_run(run, rules)
    stored = run.get("badge")
    if stored and stored != info["label"]:
        if stored == "Last One Standing":
            return {
                "label": stored, "tier": 6, "tone": "gold", "icon": "crown",
                "description": "Cleared the bank with strikes to spare — undefeated.",
                "nextLabel": None, "nextHint": "Top survival rank achieved!",
            }
        meta = _LEGACY_META.get(stored)
        if meta:
            tier, tone, icon, desc = meta
            return {
                "label": stored, "tier": tier, "tone": tone, "icon": icon,
                "description": desc,
                "nextLabel": None if tier >= 6 else info["nextLabel"],
                "nextHint": "Top rank achieved!" if tier >= 6 else info["nextHint"],
            }
        info["label"] = stored
    return info
