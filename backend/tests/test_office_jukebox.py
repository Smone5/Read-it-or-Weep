"""Office Jukebox, the music side game: its rounds, its points, and its questions."""
from __future__ import annotations

from services import engine, jukebox, settings_store

RULES = settings_store.clean_game({})
STOCK = {"mcq": 0, "picture": 0, "text": 0, "fact_fake": 0}


def test_four_rounds_of_music_with_no_report_needed():
    plan = engine.build_plan("jukebox", RULES, STOCK, [], True)
    assert [slot["slot"] for slot in plan] == ["music"] * 3 + ["jargon"] * 3 + ["song_or_jargon"] * 4 + ["music"] * 2
    assert [slot["name"] for slot in plan[::3]][:3] == ["Morning Stand-up", "Jargon Jukebox", "Song or Synergy?"]
    assert plan[0]["asks"] == "artist" and plan[-1]["asks"] == "title" and plan[-1]["mult"] == 2
    assert min(slot["limit"] for slot in plan) >= 15                       # generous clocks
    assert "jukebox" in settings_store.MODES


def test_only_a_new_round_gets_a_title_card():
    run = {"mode": "jukebox"}
    first = {"slot": "music", "round_no": 1}
    assert engine._deals_sealed(run, [], first)                           # the opening
    assert not engine._deals_sealed(run, [{"round_no": 1}], first)         # the next clip: no card
    assert engine._deals_sealed(run, [{"round_no": 1}], {"slot": "jargon", "round_no": 2})
    assert engine._deals_sealed(run, [{"round_no": 3}], {"slot": "music", "round_no": 4})


def test_nothing_costs_points():
    run = {"mode": "jukebox", "streak": 2}
    item = {"slot": "jargon", "points_multiplier": 1, "wager": None}
    assert engine._points(run, RULES, item, False, False, 0.5) == 0
    assert engine._points(run, RULES, item, False, True, 0.0) == 0
    assert engine._points(run, RULES, item, True, False, 1.0) == 100 + 20
    closing = {"slot": "music", "points_multiplier": 2, "wager": None}
    assert engine._points({**run, "streak": 0}, RULES, closing, True, False, 1.0) == 200


def test_titles_are_shown_as_people_know_them():
    assert jukebox.clean_title("Don't Stop Believin' (2022 Remaster)") == "Don't Stop Believin'"
    assert jukebox.clean_title("Take On Me - 2015 Remaster") == "Take On Me"
    assert jukebox.clean_title("Uptown Funk (feat. Bruno Mars)") == "Uptown Funk"
    assert jukebox.clean_title("Hey Jude") == "Hey Jude"


def test_song_or_synergy_has_one_right_answer_and_plays_the_song_when_it_was_one():
    tracks = [{"id": f"t{n}", "artist": f"Band {n}", "title": f"Song {n} (Remastered)", "preview_url": f"https://p/{n}"}
              for n in range(6)]
    seen = {"song": 0, "jargon": 0}
    for _ in range(60):
        item = jukebox.song_or_jargon_item(tracks, set(), set())
        assert item["payload"]["options"] == [jukebox.SONG, jukebox.JARGON]
        if item["correct_index"] == 0:
            seen["song"] += 1
            assert item["track"] and item["payload"]["preview_url"] and "(Remastered)" not in item["payload"]["text"]
        else:
            seen["jargon"] += 1
            assert item["track"] is None and item["payload"]["phrase"] in jukebox.OFFICE_JARGON
    assert seen["song"] and seen["jargon"]
    used = set(jukebox.OFFICE_JARGON[:-1])                                  # one phrase left: never asked twice
    assert jukebox.song_or_jargon_item([], set(), used)["payload"]["phrase"] == jukebox.OFFICE_JARGON[-1]


def test_the_jargon_is_unique_and_the_titles_are_earned_by_music_accuracy():
    assert len(set(jukebox.OFFICE_JARGON)) == len(jukebox.OFFICE_JARGON) >= 40
    assert jukebox.badge(12, 12) == "Chief Music Officer"
    assert jukebox.badge(9, 12) == "Senior Playlist Manager"
    assert jukebox.badge(5, 12) == "Jingle Intern"
    assert jukebox.badge(1, 12) == "Hold Music Specialist"
    assert jukebox.badge(0, 0) == "Hold Music Specialist"
