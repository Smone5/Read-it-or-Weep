"""Tests for mode-aware end-of-game ratings (badges)."""
from __future__ import annotations

from services import badges
from services.settings_store import DEFAULTS

RULES = DEFAULTS["game"]


def test_show_six_tiers_and_next_hints():
    # 100% flawless (at least 3 answered) earns Cover to Cover.
    top = badges.evaluate_report_accuracy(15, 15)
    assert top["label"] == "Cover to Cover" and top["tone"] == "legend" and top["nextLabel"] is None

    # 199/200 rounds to 100%, but is NOT flawless -> Subject Expert.
    near = badges.evaluate_report_accuracy(199, 200)
    assert near["label"] == "Subject Expert" and near["nextLabel"] == "Cover to Cover"
    assert "1 slip away" in (near["nextHint"] or "")

    # 90-99% -> Subject Expert
    expert = badges.evaluate_report_accuracy(14, 15)
    assert expert["label"] == "Subject Expert" and expert["tone"] == "gold"

    # 75-89% -> Well Read
    well = badges.evaluate_report_accuracy(12, 15)
    assert well["label"] == "Well Read" and well["tone"] == "good" and well["nextLabel"] == "Subject Expert"

    # 60-74% -> Got the Gist (no longer lumped into Skimmer!)
    gist = badges.evaluate_report_accuracy(11, 16)  # 69%
    assert gist["label"] == "Got the Gist" and gist["tone"] == "brand"
    assert gist["nextLabel"] == "Well Read"
    assert gist["nextHint"] == "1 more right answer (or 75%+) for Well Read"

    # 40-59% -> Skimmer
    skim = badges.evaluate_report_accuracy(7, 15)  # 47%
    assert skim["label"] == "Skimmer" and skim["tone"] == "warn" and skim["nextLabel"] == "Got the Gist"

    # 0-39% -> Judged It By Its Cover
    cover = badges.evaluate_report_accuracy(3, 15)  # 20%
    assert cover["label"] == "Judged It By Its Cover" and cover["tone"] == "bad"


def test_sudden_death_avoids_three_strike_accuracy_trap():
    # 9 right + 3 strikes = 75% (12 answered) -> Well Read, 3 away from Ironclad
    r1 = badges.evaluate_sudden_death(9, 12, strikes=3, max_strikes=3)
    assert r1["label"] == "Well Read" and r1["nextHint"] == "3 more right answers for Ironclad"

    # 6 right + 3 strikes = 67% -> Got the Gist (used to be Skimmer!)
    r2 = badges.evaluate_sudden_death(6, 9, strikes=3, max_strikes=3)
    assert r2["label"] == "Got the Gist" and r2["nextLabel"] == "Well Read"

    # 15 right + 3 strikes -> Ironclad
    r3 = badges.evaluate_sudden_death(15, 18, strikes=3, max_strikes=3)
    assert r3["label"] == "Ironclad" and r3["nextLabel"] == "Last One Standing"

    # 20+ right -> Last One Standing
    r4 = badges.evaluate_sudden_death(20, 23, strikes=3, max_strikes=3)
    assert r4["label"] == "Last One Standing" and r4["tone"] == "legend"

    # Clearing a 10-question deck with fewer than 3 strikes -> Last One Standing
    cleared = badges.evaluate_sudden_death(10, 10, strikes=0, max_strikes=3)
    assert cleared["label"] == "Last One Standing"

    # 1 right + 3 strikes -> Judged It By Its Cover
    early = badges.evaluate_sudden_death(1, 4, strikes=3, max_strikes=3)
    assert early["label"] == "Judged It By Its Cover"


def test_speed_run_closes_stalling_loophole():
    # Stalling at 2/2 (100%) CANNOT earn top rank — drops to Skimmer with volume hint.
    staller = badges.evaluate_speed(2, 2, clock_sec=90)
    assert staller["label"] == "Skimmer"
    assert staller["nextLabel"] == "Got the Gist"
    assert "3 more right answers" in (staller["nextHint"] or "")

    # Fast player: 12/18 (67%) -> Quick Study (needs accuracy for Rapid Reader)
    fast = badges.evaluate_speed(12, 18, clock_sec=90)
    assert fast["label"] == "Quick Study"
    assert fast["nextLabel"] == "Rapid Reader"
    assert "75%+" in (fast["nextHint"] or "")

    # Strong speed + accuracy: 13/16 (81%) -> Rapid Reader
    rapid = badges.evaluate_speed(13, 16, clock_sec=90)
    assert rapid["label"] == "Rapid Reader" and rapid["tone"] == "gold"

    # 17/19 (89%) -> Speed Demon
    demon = badges.evaluate_speed(17, 19, clock_sec=90)
    assert demon["label"] == "Speed Demon" and demon["tone"] == "legend" and demon["nextLabel"] is None


def test_jukebox_five_tiers():
    assert badges.evaluate_jukebox(12, 12)["label"] == "Chief Music Officer"
    assert badges.evaluate_jukebox(9, 12)["label"] == "Senior Playlist Manager"
    assert badges.evaluate_jukebox(8, 12)["label"] == "VP of Vibes"
    assert badges.evaluate_jukebox(5, 12)["label"] == "Jingle Intern"
    assert badges.evaluate_jukebox(2, 12)["label"] == "Hold Music Specialist"


def test_abandoned_runs_and_legacy_rows():
    abandoned = badges.evaluate_run(
        {"mode": "show", "status": "abandoned", "report_correct": 2, "report_answered": 2},
        RULES,
    )
    assert abandoned["label"] == "Warm-Up Only" and abandoned["tone"] == "muted" and abandoned["tier"] == 0

    legacy = badges.summary_badge_info(
        {"mode": "show", "status": "completed", "report_correct": 14, "report_answered": 15, "badge": "Briefing Boss"},
        RULES,
    )
    assert legacy["label"] == "Briefing Boss" and legacy["tone"] == "gold" and legacy["icon"] == "trophy"
