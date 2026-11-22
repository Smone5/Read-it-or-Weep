"""Office Jukebox: a short music side game with an office twist.

Players warm up here before the report games: four quick rounds of hits from their chosen decade, framed as a day
at the office, and nothing they answer costs them points. ("It sort of helps people build confidence getting these
right to play the other games. So it needs to be really fun." - the organiser, 2026-11-20.) The songs are ordinary
hits; the business theme is in the framing and the jokes:

  Morning Stand-up   who performed this? (an easy start)
  Jargon Jukebox     a song's title rewritten as corporate jargon - pick the real title; the song plays as the reward
  Song or Synergy?   a phrase: a hit song, or office jargon? A song plays when it was one
  Closing Bell       name the song, for double points
"""
from __future__ import annotations

import asyncio
import logging
import random
import re

import db
import llm
from config import config
from services import badges

log = logging.getLogger("jukebox")

ROUNDS = ("Morning Stand-up", "Jargon Jukebox", "Song or Synergy?", "Closing Bell")
SONG, JARGON = "HIT SONG", "OFFICE JARGON"

# Workplace-safe office jargon, none of it the title of a well-known hit (so "Song or Synergy?" has one right answer).
OFFICE_JARGON = (
    "Circle Back", "Move the Needle", "Low-Hanging Fruit", "Boil the Ocean", "Touch Base", "Deep Dive",
    "Take It Offline", "Blue-Sky Thinking", "Quick Win", "Run It Up the Flagpole", "Ducks in a Row", "Herding Cats",
    "Per My Last Email", "Action Items", "Paradigm Shift", "Thought Leadership", "Value Add", "Core Competency",
    "Leverage the Learnings", "Hard Stop", "Ping Me", "The Parking Lot", "Single Source of Truth", "Level Set",
    "Socialize the Idea", "Net-Net", "Swim Lanes", "Reply All", "Calendar Invite", "Stakeholder Alignment",
    "Deliverables", "Best Practice", "Bleeding Edge", "Win-Win", "Moving Forward", "Open the Floor", "Bandwidth",
    "Pivot to Video", "Synergy", "Granular Level", "Holistic Approach", "Mission Critical", "Drill Down",
    "Out of Scope", "Key Takeaways", "Tiger Team", "Bake It In", "Peel the Onion", "Rightsizing the Roadmap",
)

# The title a player earns, by the share of the music they got right.
BADGES = [(floor, label) for floor, label, *_ in badges.JUKEBOX_TIERS]

_JARGON_BRIEF = (
    "You rewrite famous song titles as over-the-top corporate jargon for a light-hearted office quiz. A fan must be able "
    "to work the song out once they think about it: keep the title's meaning, swap its words for business-speak.\n"
    "- 3 to 8 words, Title Case, no quotation marks, nothing else.\n"
    "- Never quote any lyrics, and never keep the title's own distinctive words.\n"
    "- Workplace-safe: meetings, emails, deadlines, processes, buzzwords. Never layoffs, firing, pay, romance, or "
    "anything an HR person would wince at.\n"
    "Examples: Don't Stop Believin' -> Maintain Stakeholder Confidence. Livin' on a Prayer -> Operating Without a "
    "Contingency Budget. I Will Survive -> Committed to Business Continuity. Walk This Way -> Follow the Documented "
    "Process. We Will Rock You -> Our Q3 Deliverables Will Impress. Here Comes the Sun -> Morning Stand-up Begins.")


def clean_title(title: str) -> str:
    """A song's title as people know it: without "(Remastered 2011)", "- Single Version", "(feat. ...)"."""
    title = re.sub(r"\s*[\(\[][^)\]]*(remaster|version|edit|mix|feat\.?|live|mono|stereo|single)[^)\]]*[\)\]]", "", title,
                   flags=re.I)
    title = re.sub(r"\s+-\s+.*(remaster|version|edit|mix|live|mono|stereo|single).*$", "", title, flags=re.I)
    return title.strip() or title


async def jargon_title(track: dict) -> str | None:
    """The song's title in corporate jargon: written by the text model the first time it is needed, then kept."""
    saved = await db.fetchval("select jargon from music_jargon where track_id = $1", track["id"])
    if saved:
        return saved
    try:
        written = await llm.text(model=config.model_fast, system=_JARGON_BRIEF, effort="none",
                                 user=f"{clean_title(track['title'])} (by {track['artist']})", max_output_tokens=60)
    except Exception as exc:  # noqa: BLE001 - another song is picked instead
        log.warning("could not write a jargon title: %s", exc)
        return None
    jargon = re.sub(r"[\"“”]", "", (written or "").strip().splitlines()[0] if written else "").strip(" .")
    if not (2 <= len(jargon.split()) <= 10) or clean_title(track["title"]).lower() in jargon.lower():
        return None
    await db.execute("insert into music_jargon (track_id, jargon) values ($1, $2) on conflict (track_id) do nothing",
                     track["id"], jargon)
    return jargon


async def prepare(tracks: list[dict], count: int = 12, avoid: set | None = None) -> None:
    """Write jargon titles ahead of time so round two stays fresh across replays without waiting."""
    if not tracks:
        return
    have = {r["track_id"] for r in await db.fetch("select track_id from music_jargon where track_id = any($1::uuid[])",
                                                  [t["id"] for t in tracks])}
    skip = avoid or set()
    unseen_ready = sum(1 for t in tracks if t["id"] in have and t["id"] not in skip)
    needed = max(count - len(have), 4 - unseen_ready)
    if needed <= 0:
        return
    missing = [t for t in tracks if t["id"] not in have and t["id"] not in skip]
    if not missing:
        missing = [t for t in tracks if t["id"] not in have]
    random.shuffle(missing)
    for track in missing[:min(needed, 6)]:
        await jargon_title(track)


def prepare_soon(tracks: list[dict], avoid: set | None = None) -> None:
    async def run() -> None:
        try:
            await prepare(tracks, avoid=avoid)
        except Exception as exc:  # noqa: BLE001
            log.warning("jargon titles not prepared: %s", exc)

    asyncio.get_running_loop().create_task(run())


def _other_titles(tracks: list[dict], track: dict, n: int = 3) -> list[str] | None:
    titles = list({clean_title(t["title"]) for t in tracks if t["artist"] != track["artist"]
                   and clean_title(t["title"]).lower() != clean_title(track["title"]).lower()})
    return random.sample(titles, n) if len(titles) >= n else None


async def jargon_item(tracks: list[dict], avoid: set) -> dict | None:
    """A Jargon Jukebox question: the corporate title, and four real ones to choose from."""
    fresh = [t for t in tracks if t["id"] not in avoid]
    if not fresh:
        fresh = list(tracks)
    random.shuffle(fresh)
    ready = {r["track_id"]: r["jargon"] for r in await db.fetch(
        "select track_id, jargon from music_jargon where track_id = any($1::uuid[])", [t["id"] for t in fresh])}
    ordered = [t for t in fresh if t["id"] in ready] + [t for t in fresh if t["id"] not in ready][:4]
    for track in ordered:
        jargon = ready.get(track["id"]) or await jargon_title(track)
        wrong = _other_titles(tracks, track)
        if not jargon or not wrong:
            continue
        options = [clean_title(track["title"]), *wrong]
        random.shuffle(options)
        return {"track": track, "correct_index": options.index(clean_title(track["title"])),
                "payload": {"title": "Translate from Corporate", "text": jargon, "options": options,
                            "preview_url": track["preview_url"],
                            "explain": f"\"{jargon}\" is corporate for \"{clean_title(track['title'])}\" by {track['artist']}."}}
    return None


def song_or_jargon_item(tracks: list[dict], avoid: set, phrases_used: set[str],
                        prefer_song: bool | None = None) -> dict | None:
    """A Song or Synergy? question: a phrase that is either a hit's title or a piece of office jargon."""
    fresh = [t for t in tracks if t["id"] not in avoid and len(clean_title(t["title"]).split()) <= 6]
    if not fresh:
        fresh = [t for t in tracks if len(clean_title(t["title"]).split()) <= 6]
    phrases = [p for p in OFFICE_JARGON if p not in phrases_used]
    pick_song = (random.random() < 0.5) if prefer_song is None else prefer_song
    if fresh and (not phrases or pick_song):
        track = random.choice(fresh)
        title = clean_title(track["title"])
        return {"track": track, "correct_index": 0,
                "payload": {"title": "Song or Synergy?", "text": f"\"{title}\"", "options": [SONG, JARGON],
                            "phrase": title, "preview_url": track["preview_url"],
                            "explain": f"A hit: \"{title}\" by {track['artist']}."}}
    if not phrases:
        return None
    phrase = random.choice(phrases)
    return {"track": None, "correct_index": 1,
            "payload": {"title": "Song or Synergy?", "text": f"\"{phrase}\"", "options": [SONG, JARGON], "phrase": phrase,
                        "explain": f"Pure office jargon. \"{phrase}\" has never topped a chart - yet."}}


def badge(music_correct: int, music_answered: int) -> str:
    return badges.evaluate_jukebox(music_correct, music_answered)["label"]
