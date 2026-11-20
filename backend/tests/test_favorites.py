"""A player's favourite artist: how their songs are mixed in, and what is kept out."""
from __future__ import annotations

from services import favorites


def song(title, artist="Chicago", year=1984, origin="favourite"):
    return {"title": title, "artist": artist, "previewUrl": f"https://audio.example/{title}", "artworkUrl": None,
            "trackUrl": None, "year": year, "from": origin}


def game_tracks(n=12):
    return [{"title": f"Hit {i}", "artist": f"Band {i}", "previewUrl": f"https://audio.example/hit{i}",
             "artworkUrl": None, "trackUrl": None} for i in range(n)]


FOUND = {"artist": "Chicago", "similar": ["Journey", "Foreigner"], "songs": [
    song("If You Leave Me Now", year=1976), song("You're the Inspiration", year=1984), song("Hard to Say I'm Sorry", year=1982),
    song("Don't Stop Believin'", "Journey", 1981, "similar"), song("Faithfully", "Journey", 1983, "similar"),
    song("Juke Box Hero", "Foreigner", 1981, "similar"), song("Cold as Ice", "Foreigner", 1977, "similar"),
]}


def test_one_song_in_four_is_from_the_favourites_and_says_why():
    mixed = favorites.mix(game_tracks(12), FOUND, "run-1", "80s")
    assert len(mixed) == 16
    for position, track in enumerate(mixed, 1):
        assert bool(track.get("because")) == (position % 4 == 0), position
        if track.get("because"):
            assert track["because"] == "Chicago"
    assert [t["title"] for t in mixed if not t.get("because")] == [f"Hit {i}" for i in range(12)]   # the game's order kept


def test_the_favourite_and_similar_artists_take_turns_with_the_decade_first():
    picked = [t for t in favorites.mix(game_tracks(12), FOUND, "run-1", "80s") if t.get("because")]
    assert [t["artist"] == "Chicago" for t in picked] == [True, False, True, False]
    assert picked[0]["title"] != "If You Leave Me Now"                       # from 1976: after the 80s songs
    assert {picked[1]["artist"], picked[3]["artist"]} == {"Journey", "Foreigner"}   # similar artists take turns


def test_the_mix_is_the_same_every_time_for_the_same_game_and_differs_between_games():
    first = favorites.mix(game_tracks(12), FOUND, "run-1", "80s")
    assert favorites.mix(game_tracks(12), FOUND, "run-1", "80s") == first
    others = [favorites.mix(game_tracks(12), FOUND, f"run-{n}", "80s") for n in range(2, 8)]
    assert any(other != first for other in others)


def test_nothing_is_added_twice_or_without_a_favourite():
    tracks = game_tracks(6) + [{"title": "Hard to Say I'm Sorry (Remastered)", "artist": "Chicago", "previewUrl": "x",
                                "artworkUrl": None, "trackUrl": None}]
    mixed = favorites.mix(tracks, FOUND, "run-1", "80s")
    keys = [favorites.song_key(t["artist"], t["title"]) for t in mixed]
    assert len(keys) == len(set(keys))                                       # the remastered one counts as the same song
    assert favorites.mix(game_tracks(8), None, "run-1", "80s") == game_tracks(8)
    assert favorites.mix(game_tracks(8), {"artist": "X", "similar": [], "songs": []}, "run-1", "80s") == game_tracks(8)


def test_the_same_song_however_it_is_spelled():
    assert favorites.song_key("Chicago", "Hard to Say I'm Sorry (Remastered)") == favorites.song_key("CHICAGO", "Hard To Say Im Sorry")
    assert favorites.song_key("Foreigner", "Juke Box Hero - 2008 Remaster") == favorites.song_key("Foreigner", "Juke Box Hero")
    assert favorites.song_key("Journey", "Faithfully") != favorites.song_key("Journey", "Separate Ways")
    assert favorites.keys(FOUND) >= {favorites.song_key("Journey", "Faithfully")}


def test_explicit_songs_and_songs_without_a_preview_are_left_out():
    def item(**attributes):
        return {"attributes": {"name": "Song", "artistName": "Artist", "releaseDate": "1985-01-01",
                               "previews": [{"url": "https://audio.example/p"}],
                               "artwork": {"url": "https://art.example/{w}x{h}bb.jpg"}, **attributes}}

    kept = favorites._apple_song(item(), "favourite")
    assert kept["previewUrl"] == "https://audio.example/p" and kept["artworkUrl"] == "https://art.example/300x300bb.jpg"
    assert kept["year"] == 1985
    assert favorites._apple_song(item(contentRating="explicit"), "favourite") is None
    assert favorites._apple_song(item(previews=[]), "favourite") is None
    assert favorites._apple_song(item(contentRating="clean"), "similar")["from"] == "similar"


def test_seasonal_songs_are_left_out():
    found = {"artist": "Ariana Grande", "similar": [], "songs": [
        song("Santa Tell Me", "Ariana Grande", 2014), song("Last Christmas", "Ariana Grande", 2013),
        song("thank u, next", "Ariana Grande", 2018)]}
    picked = [t["title"] for t in favorites.mix(game_tracks(12), found, "run-1", "10s") if t.get("because")]
    assert picked == ["thank u, next"]
