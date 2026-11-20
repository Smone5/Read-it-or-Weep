"""Music rounds: "name the artist / name the song" from a 30-second preview.

A catalogue of well-known hits per decade is kept in the database so a music
question is instant (no per-question AI or search call). The catalogue is filled in
the background: the AI lists famous songs for a decade, then each is looked up for
a preview clip.

Preview sources, in order:
  * Apple Music API – used when an Apple developer key is configured (fast, generous limits)
  * iTunes Search API – free, no account needed (about 20 look-ups a minute)
Either way the clip is Apple's standard 30-second preview, so an Apple developer
membership is optional, not required.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
from pathlib import Path

import httpx
import jwt
from pydantic import BaseModel

import db
import llm
from config import BACKEND_DIR, config

log = logging.getLogger("music")

ITUNES_SEARCH = "https://itunes.apple.com/search"
APPLE_MUSIC_SEARCH = "https://api.music.apple.com/v1/catalog/us/search"
TARGET_TRACKS = 90        # per decade: enough that music breaks and the results playlist rarely repeat
SOUNDTRACK_SONGS = 24     # about ten minutes of 30-second clips, crossfaded; the list goes round again after that
MIN_TRACKS_FOR_BANK = 6

DECADE_YEARS = {
    "50s": (1950, 1959), "60s": (1960, 1969), "70s": (1970, 1979), "80s": (1980, 1989),
    "90s": (1990, 1999), "00s": (2000, 2009), "10s": (2010, 2019), "20s": (2020, 2029),
}
DECADE_NAMES = {"50s": "1950s", "60s": "1960s", "70s": "1970s", "80s": "1980s",
                "90s": "1990s", "00s": "2000s", "10s": "2010s", "20s": "2020s"}

# Used for wrong answers before a decade's catalogue has filled in.
FALLBACK_ARTISTS = {
    "50s": ["Elvis Presley", "Chuck Berry", "Buddy Holly", "Little Richard", "Fats Domino", "The Everly Brothers"],
    "60s": ["The Beatles", "The Supremes", "The Beach Boys", "Aretha Franklin", "The Rolling Stones", "Marvin Gaye"],
    "70s": ["Fleetwood Mac", "Led Zeppelin", "Bee Gees", "Eagles", "Queen", "ABBA", "Stevie Wonder"],
    "80s": ["Madonna", "Prince", "Michael Jackson", "Whitney Houston", "U2", "Bon Jovi", "Cyndi Lauper"],
    "90s": ["Nirvana", "Backstreet Boys", "Mariah Carey", "Spice Girls", "TLC", "Alanis Morissette", "Oasis"],
    "00s": ["Coldplay", "Beyoncé", "OutKast", "Linkin Park", "Kelly Clarkson", "Rihanna", "The Killers"],
    "10s": ["Taylor Swift", "Adele", "Bruno Mars", "Ed Sheeran", "Katy Perry", "Imagine Dragons", "Lorde"],
    "20s": ["Olivia Rodrigo", "Dua Lipa", "Harry Styles", "Billie Eilish", "The Weeknd", "Lizzo", "Doja Cat"],
}

_seeding: dict[str, asyncio.Task] = {}
_cache: dict[str, tuple[float, list[dict]]] = {}


class HitList(BaseModel):
    class Hit(BaseModel):
        artist: str
        title: str
        year: int

    songs: list[Hit]


# --------------------------------------------------------------------------- look-ups

def _clean_title(title: str) -> str:
    """'Billie Jean (Remastered 2012)' -> 'Billie Jean'."""
    title = re.sub(r"\s*[\(\[][^)\]]*(remaster|version|edit|mix|mono|stereo|live|deluxe|single|from)[^)\]]*[\)\]]",
                   "", title, flags=re.I)
    return re.sub(r"\s+-\s+.*(remaster|version|edit|mix).*$", "", title, flags=re.I).strip()


def _same_artist(wanted: str, found: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and").removeprefix("the "))  # noqa: E731
    a, b = norm(wanted), norm(found)
    return bool(a and b) and (a in b or b in a)


_apple_token: tuple[float, str] | None = None


def _apple_developer_token() -> str | None:
    global _apple_token
    if _apple_token and _apple_token[0] > time.time() + 300:
        return _apple_token[1]
    if not (config.apple_team_id and config.apple_key_id):
        return None
    key: str | None = None
    if config.apple_private_key_b64:
        key = base64.b64decode(config.apple_private_key_b64).decode()
    elif config.apple_private_key_path:
        path = Path(config.apple_private_key_path)
        path = path if path.is_absolute() else BACKEND_DIR / path
        key = path.read_text() if path.is_file() else None
    if not key:
        return None
    expires = time.time() + 12 * 3600
    token = jwt.encode({"iss": config.apple_team_id, "iat": int(time.time()), "exp": int(expires)}, key,
                       algorithm="ES256", headers={"kid": config.apple_key_id})
    _apple_token = (expires, token)
    return token


async def _lookup_apple(client: httpx.AsyncClient, artist: str, title: str) -> dict | None:
    token = _apple_developer_token()
    if not token:
        return None
    response = await client.get(APPLE_MUSIC_SEARCH, params={"term": f"{artist} {title}", "types": "songs", "limit": 8},
                                headers={"Authorization": f"Bearer {token}"})
    if response.status_code in (401, 403):
        log.warning("Apple Music key rejected (HTTP %s); using iTunes previews instead", response.status_code)
        global _apple_token
        _apple_token = (time.time() + 3600, "")  # stop retrying for an hour
        return None
    response.raise_for_status()
    for song in response.json().get("results", {}).get("songs", {}).get("data", []):
        a = song.get("attributes", {})
        previews = a.get("previews") or []
        if previews and _same_artist(artist, a.get("artistName", "")) and a.get("contentRating") != "explicit":
            artwork = (a.get("artwork") or {}).get("url", "")
            return {"artist": a["artistName"], "title": _clean_title(a.get("name", title)),
                    "preview_url": previews[0]["url"],
                    "artwork_url": artwork.replace("{w}", "600").replace("{h}", "600") or None,
                    "track_url": a.get("url"), "source": "apple_music",
                    "release_year": int(a["releaseDate"][:4]) if a.get("releaseDate") else None}
    return None


async def _lookup_itunes(client: httpx.AsyncClient, artist: str, title: str) -> dict | None:
    response = await client.get(ITUNES_SEARCH, params={
        "term": f"{artist} {title}", "media": "music", "entity": "song", "limit": 8, "country": "US"})
    response.raise_for_status()
    for track in response.json().get("results", []):
        if (track.get("previewUrl") and _same_artist(artist, track.get("artistName", ""))
                and track.get("trackExplicitness") != "explicit"):
            artwork = (track.get("artworkUrl100") or "").replace("100x100", "600x600")
            return {"artist": track["artistName"], "title": _clean_title(track.get("trackName", title)),
                    "preview_url": track["previewUrl"], "artwork_url": artwork or None,
                    "track_url": track.get("trackViewUrl"), "source": "itunes",
                    "release_year": int(track["releaseDate"][:4]) if track.get("releaseDate") else None}
    return None


# --------------------------------------------------------------------------- catalogue

async def tracks_for(decade: str) -> list[dict]:
    cached = _cache.get(decade)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    rows = await db.fetch("select * from music_tracks where decade = $1 and active", decade)
    _cache[decade] = (time.monotonic(), rows)
    return rows


def ensure_decade(decade: str) -> None:
    """Start filling a decade's catalogue in the background if it is thin. Never blocks a player."""
    if decade not in DECADE_YEARS:
        return
    task = _seeding.get(decade)
    if task is None or task.done():
        _seeding[decade] = asyncio.create_task(_seed_if_needed(decade))


async def _seed_if_needed(decade: str) -> None:
    try:
        if len(await tracks_for(decade)) < TARGET_TRACKS * 0.7:
            await seed_decade(decade)
    except Exception as exc:  # noqa: BLE001 – music is optional; never crash the app over it
        log.warning("could not fill the %s music catalogue: %s", decade, exc)


async def seed_decade(decade: str, count: int = TARGET_TRACKS) -> int:
    """Top a decade's catalogue up to ``count`` tracks, asking only for artists it does not have yet."""
    existing = await tracks_for(decade)
    have = {(t["artist"].lower(), t["title"].lower()) for t in existing}
    wanted = count - len(existing)
    if wanted <= 0:
        return 0
    start, end = DECADE_YEARS[decade]
    artists = sorted({t["artist"] for t in existing})
    already = (" We already have songs by these artists, so choose other artists: " + "; ".join(artists[:150]) + "."
               if artists else "")
    hits = await llm.parse(
        model=config.model_writer,
        system=("You are a music historian building a 'name that tune' round for an office trivia game. "
                "List hugely popular hit songs that nearly everyone who was around would recognise. "
                "One song per artist. Mix genres. Radio-friendly: no explicit titles or artists known mainly "
                "for explicit lyrics. Use the original performing artist and the exact song title."),
        user=f"List {wanted + 12} such songs first released between {start} and {end}.{already}",
        schema=HitList, effort="low", max_output_tokens=6000)
    added = 0
    use_apple = _apple_developer_token() is not None
    async with httpx.AsyncClient(timeout=15) as client:
        for hit in hits.songs:
            if (hit.artist.lower(), hit.title.lower()) in have or not (start - 1 <= hit.year <= end + 1):
                continue
            found = None
            try:
                if use_apple:
                    found = await _lookup_apple(client, hit.artist, hit.title)
                if found is None:
                    found = await _lookup_itunes(client, hit.artist, hit.title)
                    await asyncio.sleep(3.1)  # stay under iTunes' ~20 look-ups per minute
            except httpx.HTTPError as exc:
                log.info("look-up failed for %s - %s: %s", hit.artist, hit.title, exc)
                await asyncio.sleep(5)
                continue
            if not found:
                continue
            result = await db.execute(
                "insert into music_tracks (decade, artist, title, preview_url, artwork_url, track_url, release_year, source)"
                " values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (decade, artist, title) do nothing",
                decade, found["artist"], found["title"], found["preview_url"], found["artwork_url"],
                found["track_url"], hit.year, found["source"])
            if result.endswith("1"):
                added += 1
                _cache.pop(decade, None)
    log.info("music catalogue %s: added %s tracks", decade, added)
    return added


# A handful of sure-fire hits per decade, looked up on demand, so the very first music break of a
# brand-new install still plays something everyone knows while the full catalogue fills in.
STARTER_HITS = {
    "50s": [("Elvis Presley", "Jailhouse Rock"), ("Chuck Berry", "Johnny B. Goode"), ("Little Richard", "Tutti Frutti"),
            ("Buddy Holly", "That'll Be the Day"), ("Jerry Lee Lewis", "Great Balls of Fire"),
            ("The Everly Brothers", "All I Have to Do Is Dream")],
    "60s": [("The Beatles", "Hey Jude"), ("The Supremes", "Stop! In the Name of Love"), ("The Beach Boys", "Good Vibrations"),
            ("Aretha Franklin", "Respect"), ("The Rolling Stones", "(I Can't Get No) Satisfaction"),
            ("Marvin Gaye", "I Heard It Through the Grapevine")],
    "70s": [("Queen", "Bohemian Rhapsody"), ("ABBA", "Dancing Queen"), ("Bee Gees", "Stayin' Alive"),
            ("Fleetwood Mac", "Go Your Own Way"), ("Stevie Wonder", "Superstition"), ("Eagles", "Hotel California")],
    "80s": [("Michael Jackson", "Billie Jean"), ("Madonna", "Like a Virgin"), ("Prince", "When Doves Cry"),
            ("Bon Jovi", "Livin' on a Prayer"), ("Cyndi Lauper", "Girls Just Want to Have Fun"), ("a-ha", "Take On Me")],
    "90s": [("Nirvana", "Smells Like Teen Spirit"), ("Backstreet Boys", "I Want It That Way"), ("Spice Girls", "Wannabe"),
            ("TLC", "Waterfalls"), ("Alanis Morissette", "Ironic"), ("Oasis", "Wonderwall")],
    "00s": [("OutKast", "Hey Ya!"), ("Beyoncé", "Crazy in Love"), ("Coldplay", "Viva La Vida"),
            ("Kelly Clarkson", "Since U Been Gone"), ("The Killers", "Mr. Brightside"), ("Rihanna", "Umbrella")],
    "10s": [("Adele", "Rolling in the Deep"), ("Taylor Swift", "Shake It Off"), ("Mark Ronson", "Uptown Funk"),
            ("Pharrell Williams", "Happy"), ("Carly Rae Jepsen", "Call Me Maybe"), ("Ed Sheeran", "Shape of You")],
    "20s": [("Dua Lipa", "Levitating"), ("Harry Styles", "As It Was"), ("The Weeknd", "Blinding Lights"),
            ("Olivia Rodrigo", "drivers license"), ("Miley Cyrus", "Flowers"), ("Lizzo", "About Damn Time")],
}


async def _store(decade: str, found: dict, year: int | None) -> dict | None:
    row = await db.fetchrow(
        "insert into music_tracks (decade, artist, title, preview_url, artwork_url, track_url, release_year, source)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (decade, artist, title) do update set active = true returning *",
        decade, found["artist"], found["title"], found["preview_url"], found["artwork_url"], found["track_url"],
        year or found.get("release_year"), found["source"])
    _cache.pop(decade, None)
    return row


async def _starter_track(decade: str, avoid_artists: set[str]) -> dict | None:
    """Cold start: look up one well-known hit for the decade right now."""
    candidates = [hit for hit in STARTER_HITS.get(decade, []) if hit[0].lower() not in avoid_artists]
    random.shuffle(candidates)
    async with httpx.AsyncClient(timeout=10) as client:
        for artist, title in candidates[:3]:
            found = await _lookup_apple(client, artist, title) or await _lookup_itunes(client, artist, title)
            if found:
                return await _store(decade, found, None)
    return None


def prefill(decades: list[str]) -> None:
    """Called at start-up: fill the catalogues one decade at a time in the background."""
    async def run() -> None:
        for decade in decades:
            await _seed_if_needed(decade)

    if "prefill" not in _seeding or _seeding["prefill"].done():
        _seeding["prefill"] = asyncio.create_task(run())


async def build_question(decade: str, avoid_track_ids: set[str]) -> dict | None:
    """Return {'track', 'text', 'options', 'correct_index'} for a music question, or None if unavailable."""
    ensure_decade(decade)
    tracks = await tracks_for(decade)
    fresh = [t for t in tracks if t["id"] not in avoid_track_ids]
    track = random.choice(fresh) if fresh else (random.choice(tracks) if len(tracks) >= MIN_TRACKS_FOR_BANK else None)
    if track is None:
        try:
            track = await _starter_track(decade, {t["artist"].lower() for t in tracks if t["id"] in avoid_track_ids})
        except httpx.HTTPError as exc:
            log.warning("no music available for %s: %s", decade, exc)
            return None
        if track is None:
            return None
        tracks = await tracks_for(decade)

    other_titles = list({t["title"] for t in tracks if t["artist"] != track["artist"] and t["title"] != track["title"]})
    ask_title = len(other_titles) >= 3 and random.random() < 0.3
    if ask_title:
        correct, wrong = track["title"], random.sample(other_titles, 3)
        text = "Name that tune. What is this song called?"
    else:
        pool = list({t["artist"] for t in tracks if not _same_artist(t["artist"], track["artist"])})
        if len(pool) < 3:
            pool = list({*pool, *[a for a in FALLBACK_ARTISTS.get(decade, []) if not _same_artist(a, track["artist"])]})
        if len(pool) < 3:
            return None
        correct, wrong = track["artist"], random.sample(pool, 3)
        text = "Who performed this track?"
    options = [correct, *wrong]
    random.shuffle(options)
    return {"track": track, "text": text, "options": options, "correct_index": options.index(correct),
            "asks": "title" if ask_title else "artist"}


def _track_view(track: dict) -> dict:
    return {"title": track["title"], "artist": track["artist"], "previewUrl": track["preview_url"],
            "artworkUrl": track["artwork_url"], "trackUrl": track["track_url"]}


def game_decade(run: dict, rules: dict) -> str | None:
    """The decade a game's background music comes from: the one it was started with, or - for a style
    of play without music breaks - one picked for that game, the same every time it is asked for."""
    if run["decade"]:
        return run["decade"]
    allowed = sorted(rules["decades"])
    return random.Random(f"decade:{run['id']}").choice(allowed) if allowed else None


async def soundtrack_rows(decade: str, seed: str, count: int = SOUNDTRACK_SONGS) -> list[dict]:
    """The songs that play quietly behind one game: one per artist, in an order fixed by ``seed``.

    Fixed, because two things must agree on it without talking to each other: the browser plays these
    songs, and the game never uses one of them as a "name that tune" question.
    """
    tracks = sorted(await tracks_for(decade), key=lambda t: t["id"])
    random.Random(f"soundtrack:{seed}").shuffle(tracks)
    chosen: list[dict] = []
    artists: set[str] = set()
    for track in tracks:
        artist = track["artist"].lower()
        if artist in artists:
            continue
        artists.add(artist)
        chosen.append(track)
        if len(chosen) >= count:
            break
    return chosen


async def soundtrack(decade: str, seed: str, skip_ids: set[str] | None = None) -> list[dict]:
    """The same list as the browser plays it, leaving out any song already used as a question."""
    return [_track_view(t) for t in await soundtrack_rows(decade, seed) if t["id"] not in (skip_ids or set())]


async def after_party(decade: str, heard_ids: list[str] | None = None, count: int = 40, seed: str | None = None) -> list[dict]:
    """A playlist for the results screen: hits from the game's decade, opening with the ones just played.

    One song per artist keeps it varied. With a ``seed`` the order is the same every time it is asked for.
    """
    tracks = await tracks_for(decade)
    by_id = {t["id"]: t for t in tracks}
    lead = [by_id[i] for i in dict.fromkeys(heard_ids or []) if i in by_id]
    rest = sorted((t for t in tracks if t not in lead), key=lambda t: t["id"])
    (random.Random(seed) if seed else random).shuffle(rest)
    chosen: list[dict] = []
    artists: set[str] = set()
    for track in [*lead, *rest]:
        artist = track["artist"].lower()
        if artist in artists:
            continue
        artists.add(artist)
        chosen.append(_track_view(track))
        if len(chosen) >= count:
            break
    return chosen


async def preview_is_dead(url: str) -> bool:
    """Ask the music host directly whether a clip still exists. True only on a clear "gone".

    A browser failing to play a clip proves nothing about the clip: an office network that blocks
    the music host fails every one of them. Without this check, one such player would retire the
    whole catalogue, a track at a time.
    """
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            response = await client.get(url, headers={"Range": "bytes=0-1"})
    except httpx.HTTPError:
        return False                      # we could not tell, so the clip keeps the benefit of the doubt
    return response.status_code in (401, 403, 404, 410)


async def retire_track(track_id: str) -> None:
    """This preview is gone for good; stop serving it."""
    row = await db.fetchrow("update music_tracks set active = false where id = $1 returning decade", track_id)
    if row:
        _cache.pop(row["decade"], None)
