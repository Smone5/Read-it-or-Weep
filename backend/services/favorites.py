"""A player's favourite artist: now and then, a song from them or from artists like them.

A player may name an artist they like (the lobby asks "Who do you like?"). About one song in four behind their
game, and on their results screen, then comes from that artist or from similar ones - Apple Music's "similar
artists" when the Apple developer key is set, the artist alone otherwise. They are still 30-second previews,
chosen first from the game's own decade when the artist has songs from it, never explicit (this is played at
work), and never used as a "name that tune" question: the questions stay the same, and fair, for everyone.

Each lookup is kept for a week (table favorite_music), so a game never waits on the catalogue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re

import httpx

import db
from services import music_service

log = logging.getLogger("favorites")

APPLE = "https://api.music.apple.com/v1/catalog/us"
ITUNES = "https://itunes.apple.com/search"
KEEP_DAYS = 7
SIMILAR_ARTISTS = 5        # artists like the favourite whose songs are mixed in
SONGS_PER_SIMILAR = 4
EVERY = 4                  # one song in this many is from the favourites
# The catalogue's "top songs" follow the season: in November they fill with Christmas songs, which would be odd
# behind a quiz in March. They are left out of the favourites (the game's own decade hits are chosen separately).
SEASONAL = re.compile(r"christmas|xmas|santa|sleigh|jingle|mistletoe|snowman|reindeer|holiday|new year|noel|navidad", re.I)


def artist_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def song_key(artist: str, title: str) -> str:
    """The same song, however the catalogue spells it ("Hold the Line (Remastered)" is "Hold the Line")."""
    title = re.sub(r"[\(\[].*?[\)\]]", "", title or "")
    title = re.split(r"\s+-\s+", title)[0]
    return artist_key(artist) + "|" + artist_key(title)


# --------------------------------------------------------------------------- the catalogue

def _apple_song(item: dict, origin: str) -> dict | None:
    attrs = item.get("attributes") or {}
    previews = attrs.get("previews") or []
    if attrs.get("contentRating") == "explicit" or not previews or not previews[0].get("url"):
        return None
    artwork = (attrs.get("artwork") or {}).get("url") or ""
    return {"title": attrs.get("name", ""), "artist": attrs.get("artistName", ""), "previewUrl": previews[0]["url"],
            "artworkUrl": artwork.replace("{w}", "300").replace("{h}", "300") or None, "trackUrl": attrs.get("url"),
            "year": int((attrs.get("releaseDate") or "0")[:4] or 0), "from": origin}


async def _from_apple(client: httpx.AsyncClient, token: str, name: str) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    found = await client.get(f"{APPLE}/search", params={"term": name, "types": "artists", "limit": 1}, headers=headers)
    artists = ((found.json().get("results") or {}).get("artists") or {}).get("data") or [] if found.status_code == 200 else []
    if not artists:
        return None
    artist = artists[0]
    page = await client.get(f"{APPLE}/artists/{artist['id']}", params={"views": "similar-artists,top-songs"},
                            headers=headers)
    if page.status_code != 200:
        return None
    views = (page.json().get("data") or [{}])[0].get("views") or {}
    similar = [(a["id"], a["attributes"]["name"]) for a in (views.get("similar-artists") or {}).get("data", [])
               if a.get("attributes")][:SIMILAR_ARTISTS]
    songs = [_apple_song(item, "favourite") for item in (views.get("top-songs") or {}).get("data", [])]

    async def top_songs(artist_id: str) -> list[dict]:
        reply = await client.get(f"{APPLE}/artists/{artist_id}", params={"views": "top-songs"}, headers=headers)
        if reply.status_code != 200:
            return []
        data = ((reply.json().get("data") or [{}])[0].get("views") or {}).get("top-songs") or {}
        return [_apple_song(item, "similar") for item in data.get("data", [])][:SONGS_PER_SIMILAR * 2]

    for result in await asyncio.gather(*(top_songs(artist_id) for artist_id, _ in similar), return_exceptions=True):
        if isinstance(result, list):
            songs += [song for song in result if song][:SONGS_PER_SIMILAR]
    return {"artist": artist["attributes"]["name"], "similar": [label for _, label in similar],
            "songs": [song for song in songs if song]}


async def _from_itunes(client: httpx.AsyncClient, name: str) -> dict | None:
    """Without an Apple developer key: the artist's own songs from the free iTunes search (no similar artists)."""
    reply = await client.get(ITUNES, params={"term": name, "entity": "song", "limit": 40})
    if reply.status_code != 200:
        return None
    wanted = artist_key(name)
    songs, names = [], {}
    for item in reply.json().get("results", []):
        artist = item.get("artistName", "")
        if wanted not in artist_key(artist) or not item.get("previewUrl") or item.get("trackExplicitness") == "explicit":
            continue
        names[artist] = names.get(artist, 0) + 1
        songs.append({"title": item.get("trackName", ""), "artist": artist, "previewUrl": item["previewUrl"],
                      "artworkUrl": (item.get("artworkUrl100") or "").replace("100x100", "300x300") or None,
                      "trackUrl": item.get("trackViewUrl"), "year": int((item.get("releaseDate") or "0")[:4] or 0),
                      "from": "favourite"})
    if not songs:
        return None
    return {"artist": max(names, key=names.get), "similar": [], "songs": songs}


def _tidy(found: dict) -> dict:
    """One of each song, and nothing without a preview."""
    seen, songs = set(), []
    for song in found["songs"]:
        key = song_key(song["artist"], song["title"])
        if song.get("previewUrl") and key not in seen:
            seen.add(key)
            songs.append(song)
    return {**found, "songs": songs}


async def is_artist_blocked(name: str) -> bool:
    policy = await music_service.music_policy()
    return music_service.is_blocked(name, "", policy)


async def pool(name: str) -> dict | None:
    """What the catalogue has for an artist: their name as the catalogue spells it, similar artists, and songs.
    None when nobody by that name is found or the artist is on the workplace blocklist. Kept for a week."""
    key = artist_key(name)
    if not key:
        return None
    policy = await music_service.music_policy()
    if music_service.is_blocked(name, "", policy):
        return None
    row = await db.fetchrow("select artist, similar_artists as similar, songs from favorite_music where artist_key = $1"
                            f" and fetched_at > now() - interval '{KEEP_DAYS} days'", key)
    if row is None:
        found = None
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                token = music_service._apple_developer_token()
                found = await _from_apple(client, token, name) if token else None
                if found is None or not found["songs"]:
                    found = await _from_itunes(client, name) or found
        except httpx.HTTPError as exc:
            log.warning("could not look up %r in the music catalogue (%s)", name, type(exc).__name__)
            return None
        found = _tidy(found) if found else {"artist": "", "similar": [], "songs": []}
        for each in {key, artist_key(found["artist"])} - {""}:
            await db.execute("insert into favorite_music (artist_key, artist, similar_artists, songs) values ($1, $2, $3, $4)"
                             " on conflict (artist_key) do update set artist = excluded.artist,"
                             " similar_artists = excluded.similar_artists,"
                             " songs = excluded.songs, fetched_at = now()",
                             each, found["artist"], json.dumps(found["similar"]), json.dumps(found["songs"]))
        row = found
    songs = row["songs"] if isinstance(row["songs"], list) else json.loads(row["songs"])
    similar = row["similar"] if isinstance(row["similar"], list) else json.loads(row["similar"])
    if not row["artist"] or music_service.is_blocked(row["artist"], "", policy):
        return None
    songs = [s for s in songs if not music_service.is_blocked(s.get("artist", ""), s.get("title", ""), policy)]
    similar = [a for a in similar if not music_service.is_blocked(a, "", policy)]
    if not songs:
        return None
    return {"artist": row["artist"], "similar": similar, "songs": songs}


async def for_player(player_id: str) -> dict | None:
    """The player's favourite, if they named one (and the catalogue has it)."""
    name = await db.fetchval("select favorite_artist from players where id = $1", player_id)
    if not name:
        return None
    try:
        return await pool(name)
    except Exception as exc:  # noqa: BLE001 – a favourite is a nicety; the game's own music always plays
        log.warning("favourite artist lookup failed: %s", exc)
        return None


# --------------------------------------------------------------------------- mixing them in

def keys(found: dict | None) -> set[str]:
    return {song_key(song["artist"], song["title"]) for song in (found or {}).get("songs", [])}


def mix(tracks: list[dict], found: dict | None, seed: str, decade: str | None) -> list[dict]:
    """The game's own songs with one in every EVERY from the favourites: the favourite artist and similar artists
    in turn, songs from the game's decade first, the same order every time for the same seed. A song that is
    already in the list is not added twice."""
    if not found or not found.get("songs"):
        return tracks
    have = {song_key(t["artist"], t["title"]) for t in tracks}
    low, high = music_service.DECADE_YEARS.get(decade or "", (0, 9999))
    rng = random.Random(f"favourites:{seed}")

    def ordered(origin: str) -> list[dict]:
        songs = [s for s in found["songs"] if s.get("from") == origin and song_key(s["artist"], s["title"]) not in have
                 and not SEASONAL.search(s.get("title") or "")]
        rng.shuffle(songs)
        in_decade = [s for s in songs if low <= int(s.get("year") or 0) <= high]
        return in_decade + [s for s in songs if s not in in_decade]

    theirs, alike = ordered("favourite"), ordered("similar")
    # Similar artists take turns, so one of them does not fill every slot.
    by_artist: dict[str, list[dict]] = {}
    for song in alike:
        by_artist.setdefault(song["artist"], []).append(song)
    groups, alike = list(by_artist.values()), []
    while any(groups):
        for group in groups:
            if group:
                alike.append(group.pop(0))
    queue: list[dict] = []
    while theirs or alike:
        for source in (theirs, alike):
            if source:
                queue.append(source.pop(0))
    out: list[dict] = []
    for index, track in enumerate(tracks, 1):
        out.append(track)
        if index % (EVERY - 1) == 0 and queue:
            song = queue.pop(0)
            out.append({"title": song["title"], "artist": song["artist"], "previewUrl": song["previewUrl"],
                        "artworkUrl": song.get("artworkUrl"), "trackUrl": song.get("trackUrl"), "because": found["artist"]})
    return out
