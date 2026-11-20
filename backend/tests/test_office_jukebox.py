"""Office Jukebox, the music side game: its rounds, its points, and its questions."""
from __future__ import annotations

from datetime import datetime, timezone

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


async def test_a_jargon_title_plays_its_song_and_song_or_synergy_is_decided_without_one():
    # The song a question is about plays under it, so the background music never stands in for it (user,
    # 2026-11-20) - except in Song or Synergy?, where hearing it would give the answer away.
    served = datetime.now(timezone.utc)

    def item(slot: str, **payload) -> dict:
        return {"id": "i-1", "position": 4, "slot": slot, "round_no": 2, "round_name": "Jargon Jukebox",
                "section_id": None, "time_limit_sec": 30, "points_multiplier": 1, "lifelines_used": [], "wager": None,
                "served_at": served, "payload": {"title": "Translate from Corporate", "text": "Cardiac Glass Ceiling",
                                                 "options": ["Heart of Glass", "b", "c", "d"], **payload}}

    run = {"mode": "jukebox"}
    riddle = await engine.public_item(item("jargon", preview_url="https://p/1"), run)
    assert riddle["music"] == {"previewUrl": "https://p/1", "asks": "title"}
    card = await engine.public_item(item("jargon", preview_url="https://p/1", sealed=True), run)
    assert card["text"] is None and card["music"]["previewUrl"] == "https://p/1"    # loads during the title card
    assert "music" not in await engine.public_item(item("song_or_jargon", preview_url="https://p/1"), run)
    # A browser that cannot play the songs gets the titles without them (see the media-error route).
    assert "music" not in await engine.public_item(item("jargon", preview_url="https://p/1", no_clip=True), run)
    assert engine._grace_ms(item("jargon")) == engine.MUSIC_GRACE_MS              # its clock waits for the song
    assert engine._grace_ms(item("song_or_jargon")) == engine.GRACE_MS


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
