"""End-to-end smoke test against the real database: plays whole games through the API.

    cd backend && .venv/bin/python -m scripts.smoke_play [quiz-slug]

Exercises every style of play, lifelines, the final wager, the leaderboard, admin
functions and a live game, asserting the rules hold (answers never leak before an
answer is given, scores add up, phases advance once). Uses throw-away players.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import db  # noqa: E402
from config import config  # noqa: E402
from main import app  # noqa: E402

FAILS: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        FAILS.append(message)


async def correct_index(item_id: str) -> int:
    return await db.fetchval("select correct_index from run_items where id = $1", item_id)


async def play(client: httpx.AsyncClient, slug: str, headers: dict, mode: str, skill: float, *,
               lifelines: bool = False, label: str = "") -> dict:
    """Play one whole game. `skill` is the probability of answering correctly."""
    response = await client.post(f"/api/quiz/{slug}/runs", json={"mode": mode, "decade": "80s"}, headers=headers)
    assert response.status_code == 200, response.text
    turn = response.json()
    run_id = turn["state"]["runId"]
    check(turn["item"] is None and not turn["finished"], f"{label}: starting a game deals nothing (no clock is running yet)")
    again = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()
    check(again["item"] is None and not again["finished"], f"{label}: re-opening the game deals nothing either")
    turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
    slots, rounds, score_seen, leaked, used = [], [], 0, False, set()
    songs: list[tuple[str, bool]] = []            # (slot, a song plays while it is on screen)
    rewards: list[tuple[str, int, bool]] = []     # (slot, right answer, a song plays once it is answered)
    sealed_rounds: list[str] = []
    guard = 0
    while not turn["finished"] and guard < 140:
        guard += 1
        item = turn["item"]
        blob = json.dumps(item)
        leaked = leaked or "correctIndex" in blob or "correct_index" in blob
        if item.get("sealed"):
            # The first item of a round arrives sealed: the browser shows its title card, then opens it.
            sealed_rounds.append("Music Break" if item["slot"] == "music" and mode != "jukebox" else item["round"]["name"])
            if len(sealed_rounds) == 1:
                check(item["text"] is None and item["options"] is None, f"{label}: a sealed item shows no question or answers")
                early = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": 0}, headers=headers)
                check(early.status_code == 400, f"{label}: a sealed item cannot be answered")
                held = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()["item"]
                check(held["id"] == item["id"] and held.get("sealed"), f"{label}: asking again returns the same sealed item")
                await asyncio.sleep(1.2)                       # a title card's worth of waiting...
            r = await client.post(f"/api/runs/{run_id}/open", json={"itemId": item["id"]}, headers=headers)
            assert r.status_code == 200, r.text
            item = r.json()["item"]
            if len(sealed_rounds) == 1 and item["timeLimitSec"]:
                full = item["timeLimitSec"] * 1000             # ...costs the player nothing
                check(item["remainingMs"] == full, f"{label}: the clock starts when the item is opened ({item['remainingMs']} of {full} ms)")
            blob = json.dumps(item)
            leaked = leaked or "correctIndex" in blob or "correct_index" in blob
            if len(sealed_rounds) <= 2:
                check(bool(item["text"]) and not item.get("sealed"), f"{label}: opening '{sealed_rounds[-1]}' reveals the question")
        if item.get("needsWager"):
            check(item["text"] is None and item["options"] is None, f"{label}: final question is hidden until a wager is placed")
            amount = item["wagerOptions"][-1]
            r = await client.post(f"/api/runs/{run_id}/wager", json={"itemId": item["id"], "amount": amount, "readAloud": True},
                                  headers=headers)
            assert r.status_code == 200, r.text
            item = r.json()["item"]
            check(item["text"] and item["wager"] == amount, f"{label}: wager of {amount} accepted and question revealed")
            check(item["options"] is None and item.get("startsInMs", 0) > 0,
                  f"{label}: final question held for the host's reading ({item.get('startsInMs')} ms), answers hidden")
            early = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": 0}, headers=headers)
            check(early.status_code == 400, f"{label}: an answer before the answers are up is refused ({early.status_code})")
            r = await client.post(f"/api/runs/{run_id}/start-clock", json={"itemId": item["id"]}, headers=headers)
            assert r.status_code == 200, r.text
            item = r.json()["item"]
            check(bool(item["options"]) and item["remainingMs"] == item["timeLimitSec"] * 1000,
                  f"{label}: after the reading the answers appear with the full clock ({item['remainingMs']} ms)")
        slots.append(item["slot"])
        songs.append((item["slot"], bool(item.get("music"))))
        if not rounds or rounds[-1] != item["round"]["name"]:
            rounds.append(item["round"]["name"])
        right = await correct_index(item["id"])
        if lifelines and item["slot"] in ("report", "image"):
            for kind in ("fifty", "peek", "hint"):
                if kind in used:
                    continue
                r = await client.post(f"/api/runs/{run_id}/lifeline", json={"itemId": item["id"], "kind": kind}, headers=headers)
                if r.status_code == 200:
                    used.add(kind)
                    got = r.json()["item"]
                    item = {**item, "removed": got["removed"]}       # so a wrong answer is never one that was taken away
                    if kind == "fifty":
                        check(len(got["removed"]) == 2 and right not in got["removed"], f"{label}: 50/50 removes two wrong answers")
                    elif kind == "peek":
                        check(bool(got["peek"]), f"{label}: peek returns the passage ({len(got['peek'] or '')} chars)")
                    else:
                        check(bool(got["hint"]), f"{label}: hint returned: {got['hint'][:70]!r}")
                    refunded = bool(got.get("hintRefunded"))         # a generic fallback hint costs nothing
                    check(got["multiplier"] < 1 or refunded,
                          f"{label}: {kind} reduces the points on offer (x{got['multiplier']}{', fallback hint refunded' if refunded else ''})")
                    break
        if random.random() < skill:
            choice = right
        else:
            options = [i for i in range(len(item["options"])) if i != right and i not in item.get("removed", [])]
            choice = random.choice(options)
        r = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": choice}, headers=headers)
        assert r.status_code == 200, r.text
        reveal = r.json()
        assert reveal["correct"] == (choice == right), "server marking disagrees with the stored answer"
        rewards.append((item["slot"], right, bool((reveal.get("music") or {}).get("previewUrl"))))
        if mode == "jukebox":
            assert reveal["points"] >= 0, f"{label}: an Office Jukebox answer cost points ({reveal['points']})"
        score_seen = reveal["state"]["score"]
        if guard == 1:
            again = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": right}, headers=headers)
            check(again.json()["selectedIndex"] == choice, f"{label}: a second answer to the same question is ignored")
        if reveal["finished"]:
            break
        turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
    summary = (await client.get(f"/api/runs/{run_id}/summary", headers=headers)).json()["summary"]
    check(not leaked, f"{label}: no answer key in anything sent before answering")
    # Unused lifelines pay a bonus (UNUSED_LIFELINE_BONUS each) when a Game Show or Sudden Death game finishes.
    bonus = summary["score"] - score_seen
    unused = sum(max(0, int(v)) for v in (await db.fetchval("select lifelines from runs where id = $1", run_id) or {}).values())
    expected = unused * 40 if summary["status"] == "completed" and mode in ("show", "sudden_death") else 0
    check(bonus == expected or (mode == "sudden_death" and bonus in (0, expected)),
          f"{label}: final score {summary['score']} is the running score {score_seen} plus the unused-lifeline bonus {bonus}")
    print(f"       {label}: {len(slots)} items, rounds={rounds}, score={summary['score']}, accuracy={summary['accuracy']}%, "
          f"badge={summary['badge']!r}, readiness={summary['readiness']['cleared']}/{summary['readiness']['total']}")
    return {"summary": summary, "slots": slots, "rounds": rounds, "run_id": run_id, "sealed": sealed_rounds,
            "songs": songs, "rewards": rewards}


async def jukebox_song_trouble(client: httpx.AsyncClient, slug: str, headers: dict) -> None:
    """A jargon title whose song will not play: a dead clip is swapped for another song, and a browser that cannot
    play songs at all keeps the titles without them (and loses the clip questions), instead of failing each one."""
    from services import music_service

    real = music_service.preview_is_dead, music_service.retire_track, music_service.tracks_for
    retired: list[str] = []

    async def dead(url: str) -> bool:
        return True

    async def fine(url: str) -> bool:
        return False

    async def retire(track_id: str) -> None:
        retired.append(track_id)                   # not in the real catalogue: the clip is not really gone...

    async def catalogue(decade: str) -> list[dict]:
        return [t for t in await real[2](decade) if t["id"] not in retired]     # ...but it is gone from this game

    async def opened(item: dict, run_id: str) -> dict:
        if not item.get("sealed"):
            return item
        return (await client.post(f"/api/runs/{run_id}/open", json={"itemId": item["id"]}, headers=headers)).json()["item"]

    try:
        r = await client.post(f"/api/quiz/{slug}/runs", json={"mode": "jukebox", "decade": "80s"}, headers=headers)
        run_id = r.json()["state"]["runId"]
        turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
        while turn["item"]["slot"] != "jargon":        # through the Morning Stand-up
            item = await opened(turn["item"], run_id)
            await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": 0}, headers=headers)
            turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
        card = turn["item"]
        check(bool(card.get("sealed")) and card["music"]["previewUrl"].startswith("https://"),
              "jargon jukebox: its title card brings the first title's song, to load during the card")
        riddle = await opened(card, run_id)
        check(bool(riddle["text"]) and riddle["music"]["previewUrl"] == card["music"]["previewUrl"],
              "a jargon title plays its song while it is on screen")

        music_service.preview_is_dead, music_service.retire_track, music_service.tracks_for = dead, retire, catalogue
        swapped =(await client.post(f"/api/runs/{run_id}/media-error", json={"itemId": riddle["id"]}, headers=headers)).json()
        swapped = await opened(swapped["item"], run_id)
        check(swapped["slot"] == "jargon" and swapped["id"] != riddle["id"] and len(retired) == 1
              and swapped["music"]["previewUrl"] != riddle["music"]["previewUrl"],
              "a jargon title whose song is gone is dealt again, with another song")

        music_service.preview_is_dead = fine
        kept = (await client.post(f"/api/runs/{run_id}/media-error", json={"itemId": swapped["id"]}, headers=headers)).json()
        kept = kept["item"]
        check(kept["id"] == swapped["id"] and kept["text"] == swapped["text"] and "music" not in kept,
              "a browser that cannot play the song keeps the title, without it")
        plan = await db.fetchval("select plan from runs where id = $1", run_id)
        check(bool(plan) and all(slot["slot"] != "music" and slot.get("no_clip") for slot in plan),
              f"...and the rest of its game goes on without songs ({[slot['slot'] for slot in plan]})")
        item, quiet, answered, finished = kept, True, [], False
        for _ in range(20):
            item = await opened(item, run_id)
            quiet = quiet and "music" not in item
            r = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": 0}, headers=headers)
            quiet = quiet and not (r.json().get("music") or {}).get("previewUrl")
            answered.append(item["slot"])
            turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
            if turn["finished"]:                       # (this style ends on the deal after its last answer)
                finished = True
                break
            item = turn["item"]
        check(quiet and finished and answered.count("jargon") == 3 and answered.count("song_or_jargon") == 4,
              f"no song is tried again, and the game plays on to its end ({answered})")
    finally:
        music_service.preview_is_dead, music_service.retire_track, music_service.tracks_for = real


async def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "q3-review"
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
            print("== public ==")
            check((await client.get("/api/health")).json() == {"ok": True}, "health")
            info = (await client.get(f"/api/quiz/{slug}")).json()
            check(info["quiz"]["ready"], f"quiz '{info['quiz']['title']}' is ready with {info['quiz']['questions']} questions")

            print("== hearing the hosts before choosing ==")
            # Recorded once and kept: after the first run of this test these cost nothing.
            for choice in info["branding"]["host"]["choices"]:
                began = time.monotonic()
                r = await client.get(f"/api/quiz/{slug}/hosts/{choice['id']}/sample")
                took = time.monotonic() - began
                check(r.status_code == 200 and r.headers["content-type"] in ("audio/mpeg", "audio/wav") and len(r.content) > 8000,
                      f"{choice['name']} ({choice.get('label') or choice['register']}) can be heard: "
                      f"{r.headers.get('content-type')}, {len(r.content) // 1024} KB, {took:.1f}s")
                if r.status_code == 200:
                    again = await client.get(f"/api/quiz/{slug}/hosts/{choice['id']}/sample",
                                             headers={"If-None-Match": r.headers["etag"]})
                    check(again.status_code == 304, f"  and the browser's copy is used the second time ({again.status_code})")
            check((await client.get(f"/api/quiz/{slug}/hosts/nobody/sample")).status_code == 404, "an unknown host is not found")
            check((await client.get("/api/quiz/no-such-quiz/hosts/max/sample")).status_code == 404,
                  "nor is a host of a quiz that does not exist")

            print("== admin ==")
            r = await client.post("/api/admin/login", json={"email": config.bootstrap_admin_email, "password": "wrong-password"})
            check(r.status_code == 401, "wrong admin password is rejected")
            r = await client.post("/api/admin/login", json={"email": config.bootstrap_admin_email,
                                                            "password": config.bootstrap_admin_password})
            check(r.status_code == 200, "admin can sign in")
            admin = {"Authorization": f"Bearer {r.json()['token']}"}
            check((await client.get("/api/admin/quizzes")).status_code == 401, "admin API refuses anonymous callers")
            quizzes = (await client.get("/api/admin/quizzes", headers=admin)).json()["quizzes"]
            quiz_id = next(q["id"] for q in quizzes if q["slug"] == slug)
            r = await client.patch(f"/api/admin/quizzes/{quiz_id}", headers=admin, json={
                "settings": {"modes": ["show", "sudden_death", "speed", "straight", "jukebox"]}, "teams": ["Sales", "Operations"]})
            check(r.status_code == 200 and len(r.json()["rules"]["modes"]) == 5, "all five styles of play switched on")

            print("== joining ==")
            tag = uuid.uuid4().hex[:6]
            # A quiz may be behind an access code (it is while testers are invited). The code is read here
            # and sent with each join; it is never printed.
            access = await db.fetchval("select access_code from quizzes where id = $1", quiz_id)
            entry = {"accessCode": access} if access else {}
            if access:
                r = await client.post(f"/api/quiz/{slug}/join", json={
                    "name": "Dana Reyes", "email": f"dana.{tag}@example.com", "team": "Sales", "accessCode": "not-the-code"})
                check(r.status_code == 403, "joining with the wrong access code is refused")
            r = await client.post(f"/api/quiz/{slug}/join", json={"name": "Dana Reyes", "email": f"dana.{tag}@example.com", **entry})
            check(r.status_code == 422, "joining without a team is refused when teams are set")
            players = {}
            for name, team in (("Dana Reyes", "Sales"), ("Omar Haddad", "Operations"), ("Li Wei", "Sales")):
                r = await client.post(f"/api/quiz/{slug}/join", json={
                    "name": name, "email": f"{name.split()[0].lower()}.{tag}@example.com", "team": team, **entry})
                assert r.status_code == 200, r.text
                players[name] = {"Authorization": f"Bearer {r.json()['token']}"}
            check(len(players) == 3, "three players joined with name + email only")
            check((await client.post(f"/api/quiz/{slug}/runs", json={})).status_code == 401, "cannot play without joining")

            print("== Game Show ==")
            show = await play(client, slug, players["Dana Reyes"], "show", 0.75, lifelines=True, label="show/Dana")
            for expected in ("Warm-Up", "Picture Round", "The Deep End", "Lightning Round", "Final Wager"):
                check(expected in show["rounds"], f"show has a '{expected}' round")
            check("music" in show["slots"], "show includes a music break")
            # (The final is held back by its wager rather than a seal, so it is not in this list.)
            check(show["sealed"] == [name for name in show["rounds"] if name != "Final Wager"],
                  f"every round of the show opens behind a title card: {show['sealed']}")
            party = show["summary"]["afterParty"]
            heard = [r["title"] for r in await db.fetch(
                "select t.title from run_items ri join music_tracks t on t.id = ri.music_track_id where ri.run_id = $1"
                " order by ri.position", show["run_id"])]
            check(bool(party) and party["decade"] == "80s" and len(party["tracks"]) >= 5,
                  f"results screen gets a playlist of {len(party['tracks']) if party else 0} hits from the game's decade")
            check([t["title"] for t in party["tracks"][:len(heard)]] == heard,
                  f"the playlist opens with the songs from this game's music breaks: {heard}")
            check(len({t["artist"].lower() for t in party["tracks"]}) == len(party["tracks"]) and all(
                t["previewUrl"].startswith("https://") for t in party["tracks"]), "playlist: one song per artist, all playable")
            # The soundtrack that played behind that game: the same list every time it is asked for, in the game's
            # decade, and never a song the game went on to ask about.
            music = (await client.get(f"/api/runs/{show['run_id']}/soundtrack", headers=players["Dana Reyes"])).json()
            again = (await client.get(f"/api/runs/{show['run_id']}/soundtrack", headers=players["Dana Reyes"])).json()
            behind = [t["title"] for t in music["tracks"]]
            check(music["decade"] == "80s" and len(behind) >= 10 and music["volume"] in ("quiet", "medium", "loud"),
                  f"the game has a soundtrack of {len(behind)} hits from its decade")
            check(music == again, "the soundtrack is the same list every time it is asked for")
            check(not set(behind) & set(heard), "no song from the soundtrack was used as a name-that-tune question")
            check((await client.get(f"/api/runs/{show['run_id']}/soundtrack", headers=players["Omar Haddad"])).status_code == 404,
                  "another player cannot ask for this game's soundtrack")

            print("== a favourite artist ==")
            r = await client.put("/api/me/favourite-artist", json={"artist": "  chicago "}, headers=players["Dana Reyes"])
            liked = r.json() if r.status_code == 200 else {}
            check(r.status_code == 200 and liked.get("artist") == "Chicago" and liked.get("similar"),
                  f"a favourite artist is found and kept as the catalogue spells it: {liked}")
            mixed = (await client.get(f"/api/runs/{show['run_id']}/soundtrack", headers=players["Dana Reyes"])).json()["tracks"]
            because = [i for i, t in enumerate(mixed, 1) if t.get("because")]
            check(because and all(i % 4 == 0 for i in because) and len(mixed) > len(music["tracks"]),
                  f"one song in four is from the favourites ({len(because)} of {len(mixed)}, at {because[:4]}...)")
            check(all(t["because"] == "Chicago" and t["previewUrl"].startswith("https://") for t in mixed if t.get("because")),
                  "each says why it is there, and has a preview")
            me = (await client.get(f"/api/quiz/{slug}", headers=players["Dana Reyes"])).json()["player"]
            check(me.get("favoriteArtist") == "Chicago", "the lobby remembers it")
            r = await client.put("/api/me/favourite-artist", json={"artist": "zzqx nobody by this name"}, headers=players["Dana Reyes"])
            check(r.status_code == 404, "an artist nobody has heard of is refused, kindly")
            r = await client.put("/api/me/favourite-artist", json={"artist": ""}, headers=players["Dana Reyes"])
            plain = (await client.get(f"/api/runs/{show['run_id']}/soundtrack", headers=players["Dana Reyes"])).json()["tracks"]
            check(r.status_code == 200 and not any(t.get("because") for t in plain), "clearing it brings back the decade alone")
            check((await client.put("/api/me/favourite-artist", json={"artist": "Chicago"})).status_code == 401,
                  "only a signed-in player can set one")
            await play(client, slug, players["Omar Haddad"], "show", 0.4, label="show/Omar")
            second = await play(client, slug, players["Dana Reyes"], "show", 0.9, label="show/Dana again")
            first_ids = {r["question_id"] for r in await db.fetch(
                "select question_id from run_items where run_id = $1 and slot in ('report','image')", show["run_id"])}
            second_ids = {r["question_id"] for r in await db.fetch(
                "select question_id from run_items where run_id = $1 and slot in ('report','image')", second["run_id"])}
            overlap = len(first_ids & second_ids)
            check(overlap <= 2, f"a replay asks different questions (overlap {overlap} of {len(second_ids)})")

            print("== Sudden Death ==")
            sudden = await play(client, slug, players["Li Wei"], "sudden_death", 0.55, label="sudden/Li")
            strikes = await db.fetchval("select strikes from runs where id = $1", sudden["run_id"])
            check(strikes == 3, f"sudden death ended on the third strike (strikes={strikes})")
            check(sudden["sealed"][0] == "Level 1" and set(sudden["sealed"][1:]) <= {"Music Break"},
                  f"sudden death pauses only for its opening and its music breaks: {sudden['sealed']}")

            print("== Straight Quiz ==")
            straight = await play(client, slug, players["Omar Haddad"], "straight", 0.9, label="straight/Omar")
            check(set(straight["slots"]) <= {"report", "image"}, "straight quiz has no music or lightning round")
            check(straight["summary"]["passed"] is not None, f"straight quiz reports pass/fail (passed={straight['summary']['passed']})")
            check(straight["sealed"] == ["The Quiz"], "straight quiz has one title card, at the start")
            check(straight["summary"]["afterParty"] is None, "straight quiz ends without music")
            quiet = (await client.get(f"/api/runs/{straight['run_id']}/soundtrack", headers=players["Omar Haddad"])).json()
            check(quiet["tracks"] == [], "straight quiz has no soundtrack either")

            print("== Office Jukebox ==")
            box = await play(client, slug, players["Dana Reyes"], "jukebox", 0.6, label="jukebox/Dana")
            check([box["slots"].count(kind) for kind in ("music", "jargon", "song_or_jargon")] == [5, 3, 4],
                  f"office jukebox: 5 clips, 3 jargon titles, 4 song-or-jargon ({box['slots']})")
            check(box["sealed"] == ["Morning Stand-up", "Jargon Jukebox", "Song or Synergy?", "Closing Bell"],
                  f"office jukebox opens each of its four rounds with a title card: {box['sealed']}")
            check(box["summary"]["music"]["answered"] == 12 and box["summary"]["report"]["answered"] == 0,
                  "office jukebox is all music, no report questions")
            check(box["summary"]["badge"] in {"Chief Music Officer", "Senior Playlist Manager", "VP of Vibes",
                                              "Jingle Intern", "Hold Music Specialist"},
                  f"office jukebox earns its own title ({box['summary']['badge']})")
            jargon = await db.fetch("select payload from run_items where run_id = $1 and slot = 'jargon'", box["run_id"])
            print("       jargon titles:", [row["payload"]["text"] for row in jargon])
            check(all(plays for slot, plays in box["songs"] if slot in ("music", "jargon")),
                  "office jukebox: every clip question and jargon title plays its song while it is on screen")
            first = (await client.post(f"/api/quiz/{slug}/runs", json={"mode": "jukebox", "decade": "80s"},
                                       headers=players["Li Wei"])).json()["state"]
            check(first["remaining"] == 12, f"office jukebox counts its questions: 12 to come ({first['remaining']})")
            await client.post(f"/api/runs/{first['runId']}/quit", headers=players["Li Wei"])
            check(not any(plays for slot, plays in box["songs"] if slot == "song_or_jargon"),
                  "office jukebox: nothing plays while Song or Synergy? is decided (it would give the answer away)")
            check(all(plays == (right == 0) for slot, right, plays in box["rewards"] if slot == "song_or_jargon"),
                  "office jukebox: once answered, Song or Synergy? plays the song - when it was one")
            await jukebox_song_trouble(client, slug, players["Omar Haddad"])

            print("== Speed Run ==")
            r = await client.post(f"/api/quiz/{slug}/runs", json={"mode": "speed"}, headers=players["Li Wei"])
            turn = r.json()
            run_id = turn["state"]["runId"]
            check("deadlineAt" not in turn["state"] and turn["state"]["clockSec"] >= 30,
                  f"speed run: the clock ({turn['state']['clockSec']}s) is not running during the opening")
            sealed = (await client.post(f"/api/runs/{run_id}/next", headers=players["Li Wei"])).json()
            check(sealed["item"]["sealed"] and "deadlineAt" not in sealed["state"], "speed run: dealing the first question does not start it")
            await asyncio.sleep(1.0)
            opened = (await client.post(f"/api/runs/{run_id}/open", json={"itemId": sealed["item"]["id"]},
                                        headers=players["Li Wei"])).json()
            before = opened["state"]["remainingMs"]
            check(before > turn["state"]["clockSec"] * 1000 - 500, f"speed run: the full clock starts when question one opens ({before} ms)")
            item = opened["item"]
            wrong = (await correct_index(item["id"]) + 1) % len(item["options"])
            reveal = (await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": wrong},
                                        headers=players["Li Wei"])).json()
            check(reveal["state"]["remainingMs"] < before - 2500, "a wrong answer costs time on the clock")
            await db.execute("update runs set deadline_at = now() - interval '1 second' where id = $1", run_id)
            turn = (await client.post(f"/api/runs/{run_id}/next", headers=players["Li Wei"])).json()
            check(turn["finished"], "speed run ends when the clock runs out")
            fast = (await client.get(f"/api/runs/{run_id}/soundtrack", headers=players["Li Wei"])).json()
            ending = (await client.get(f"/api/runs/{run_id}/summary", headers=players["Li Wei"])).json()["summary"]["afterParty"]
            check(len(fast["tracks"]) >= 10 and bool(ending) and ending["decade"] == fast["decade"],
                  f"speed run: a soundtrack from one decade ({fast['decade']}) that carries on behind its results")

            print("== leaderboard + readiness ==")
            board = (await client.get(f"/api/quiz/{slug}/leaderboard?mode=show", headers=players["Dana Reyes"])).json()
            check(board["you"] is not None and board["entries"][0]["score"] >= board["entries"][-1]["score"],
                  f"leaderboard ranks {board['players']} players; Dana is #{board['you']['rank']}")
            check(all("@" not in e["name"] and len(e["name"].split()) <= 2 for e in board["entries"]),
                  f"leaderboard shows short names only (e.g. {board['entries'][0]['name']!r})")
            check(len(board["teams"]) >= 1, f"team standings: {board['teams']}")
            me = (await client.get(f"/api/quiz/{slug}", headers=players["Dana Reyes"])).json()["player"]
            check(me["readiness"]["cleared"] > 0, f"Dana's readiness: {me['readiness']['cleared']}/{me['readiness']['total']} sections")

            print("== question bank management ==")
            bank_rows = (await client.get(f"/api/admin/quizzes/{quiz_id}/questions", headers=admin)).json()
            check(len(bank_rows["questions"]) >= 60 and len(bank_rows["images"]) == 5,
                  f"bank lists {len(bank_rows['questions'])} questions and {len(bank_rows['images'])} pictures")
            section_id = next(q["sectionId"] for q in bank_rows["questions"] if q["sectionId"])
            r = await client.post(f"/api/admin/quizzes/{quiz_id}/questions", headers=admin, json={
                "kind": "mcq", "text": "Which city hosts Meridian's newest cross-dock?", "options":
                ["Phoenix", "Reno", "Dallas", "Atlanta"], "correctIndex": 0, "sectionId": section_id, "pinned": True})
            check(r.status_code == 200, "admin can add a hand-written question")
            manual_id = r.json()["id"]
            r = await client.post(f"/api/admin/quizzes/{quiz_id}/questions", headers=admin, json={
                "kind": "mcq", "text": "A question with too few answers?", "options": ["One", "Two"], "correctIndex": 0})
            check(r.status_code == 422, "a hand-written question needs four different answers")
            r = await client.patch(f"/api/admin/questions/{manual_id}", headers=admin, json={"text": "Where is Meridian's newest cross-dock?"})
            listed = (await client.get(f"/api/admin/quizzes/{quiz_id}/questions", headers=admin)).json()["questions"]
            mine = next(q for q in listed if q["id"] == manual_id)
            check(mine["origin"] == "manual" and mine["pinned"] and mine["edited"], "manual question is labelled, pinned and marked edited")
            pinned = await play(client, slug, players["Li Wei"], "show", 0.8, label="show/Li (pinned check)")
            asked = await db.fetchval("select 1 from run_items where run_id = $1 and question_id = $2", pinned["run_id"], manual_id)
            check(bool(asked), "a pinned question is asked in the next game")
            r = await client.post(f"/api/admin/quizzes/{quiz_id}/questions/suggest", headers=admin, json={
                "text": "What was Meridian's operating margin in Q3 FY2026?", "correctAnswer": "8.9%"})
            check(r.status_code == 200 and len(r.json()["wrongAnswers"]) == 3,
                  f"AI suggests wrong answers: {r.json().get('wrongAnswers')} title={r.json().get('title')!r}")
            await client.delete(f"/api/admin/questions/{manual_id}", headers=admin)

            print("== flagging ==")
            victim = await db.fetchrow("select ri.id, ri.question_id from run_items ri where ri.run_id = $1"
                                       " and ri.question_id is not null limit 1", show["run_id"])
            other = await db.fetchrow("select ri.id from run_items ri join runs r on r.id = ri.run_id"
                                      " where ri.question_id = $1 and r.player_id <> (select player_id from runs where id = $2) limit 1",
                                      victim["question_id"], show["run_id"])
            await client.post("/api/questions/flag", json={"itemId": victim["id"], "reason": "Two answers look right"}, headers=players["Dana Reyes"])
            status = await db.fetchval("select status from questions where id = $1", victim["question_id"])
            check(status == "approved", "one flag alone does not pull a question")
            await db.execute("update questions set flag_count = flag_count + 1 where id = $1", victim["question_id"])
            from services import stats as stats_service
            await stats_service.note_question_health(victim["question_id"])
            status = await db.fetchval("select status from questions where id = $1", victim["question_id"])
            check(status == "pending", "a second flag pulls the question for review")
            await client.patch(f"/api/admin/questions/{victim['question_id']}", headers=admin, json={"status": "approved"})

            print("== results ==")
            rated = (await client.get(f"/api/admin/quizzes/{quiz_id}/results", headers=admin)).json()["feedback"]
            for stars in (5, 2, 4):    # one tap on the stars, then two changes of mind
                r = await client.post("/api/feedback", json={"runId": show["run_id"], "stars": stars}, headers=players["Dana Reyes"])
                assert r.status_code == 200, r.text
            kept = [row["stars"] for row in await db.fetch("select stars from feedback where run_id = $1", show["run_id"])]
            check(kept == [4], f"a player who changes their rating has one rating, the last one: {kept}")
            r = await client.post("/api/feedback", json={"runId": show["run_id"], "stars": 1}, headers=players["Omar Haddad"])
            check(r.status_code == 404, "nobody can rate someone else's game")
            results = (await client.get(f"/api/admin/quizzes/{quiz_id}/results", headers=admin)).json()
            check(results["feedback"]["count"] == rated["count"] + 1,
                  f"the admin counts it once ({rated['count']} -> {results['feedback']['count']} ratings)")
            check(len(results["participants"]) >= 3, f"results list {len(results['participants'])} participants, {results['ready']} ready")
            csv_text = (await client.get(f"/api/admin/quizzes/{quiz_id}/results.csv", headers=admin)).text
            check(csv_text.startswith("Name,Email,Team,Ready"), "results export as CSV")
            weakest = results["insights"]["weakestSections"]
            check(bool(results["insights"]["sections"]), f"insights: weakest sections = {[(s['heading'], s['accuracy']) for s in weakest]}")

            print("== host sarcasm samples ==")
            for level in (0, 5, 10):
                r = await client.post("/api/admin/host/sample", headers=admin, json={"sarcasm": level, "name": "Max"})
                check(r.status_code == 200 and r.json()["line"], f"sarcasm {level:>2}: {r.json().get('line', r.text)!r}")

            print("== live game ==")
            r = await client.post(f"/api/admin/quizzes/{quiz_id}/live", headers=admin, json={"decade": "80s"})
            assert r.status_code == 200, r.text
            session = r.json()
            code, host = session["code"], {"X-Host-Key": session["hostKey"]}
            check(len(code) == 5 and session["questions"] >= 5, f"live game {code} created with {session['questions']} questions")
            check((await client.post(f"/api/live/{code}/control", json={"action": "next"})).status_code == 403,
                  "only the host screen can control a live game")
            for headers in players.values():
                assert (await client.post(f"/api/live/{code}/join", json={}, headers=headers)).status_code == 200
            r = await client.post(f"/api/live/{code}/join", json={"name": "Walk In", "email": f"walkin.{tag}@example.com", "team": "Sales"})
            check(r.status_code == 200 and r.json()["token"], "someone who never did the pre-read can join live with name + email")
            walk_in = {"Authorization": f"Bearer {r.json()['token']}"}
            lobby = (await client.get(f"/api/live/{code}/state", headers=host)).json()
            prepared = [p["name"] for p in lobby["roster"] if p["prepared"]]
            check(lobby["players"] == 4, f"lobby shows 4 players; did the reading: {prepared}")
            state = (await client.post(f"/api/live/{code}/control", json={"action": "next"}, headers=host)).json()
            check(state["status"] == "question" and "correctIndex" not in json.dumps(state), "question phase hides the answer")
            early = await client.post(f"/api/live/{code}/answer", json={"choiceIndex": 0}, headers=players["Dana Reyes"])
            check(early.status_code == 409, "answers are refused while the host is still reading the question")
            lead = state["question"]["opensAt"]
            check((await client.post(f"/api/live/{code}/control", json={"action": "open"})).status_code == 403,
                  "only the host screen can open a question")
            opened = (await client.post(f"/api/live/{code}/control", json={"action": "open"}, headers=host)).json()
            check(opened["question"]["opensAt"] < lead, "the host screen opens the question as soon as it has been read out")
            again = (await client.post(f"/api/live/{code}/control", json={"action": "open"}, headers=host)).json()
            check(again["question"]["opensAt"] == opened["question"]["opensAt"], "opening twice changes nothing")
            await asyncio.sleep(1.1)
            items = await db.fetchval("select items from live_sessions where code = $1", code)
            right = items[0]["correct_index"]
            for name, headers in players.items():
                choice = right if name != "Omar Haddad" else (right + 1) % 4
                assert (await client.post(f"/api/live/{code}/answer", json={"choiceIndex": choice}, headers=headers)).status_code == 200
            mid = (await client.get(f"/api/live/{code}/state", headers=host)).json()
            check(mid["status"] == "question" and mid["answersIn"] == 3, "host sees 3 of 4 answers in, question still open")
            await client.post(f"/api/live/{code}/answer", json={"choiceIndex": right}, headers=walk_in)
            state = (await client.get(f"/api/live/{code}/state", headers=players["Omar Haddad"])).json()
            check(state["status"] == "reveal", "question closes as soon as everyone has answered")
            check(state["yourAnswer"]["correct"] is False and state["yourAnswer"]["points"] == 0, "wrong answer scores nothing")
            dana = (await client.get(f"/api/live/{code}/state", headers=players["Dana Reyes"])).json()
            check(dana["yourAnswer"]["points"] >= 500, f"correct answer scores {dana['yourAnswer']['points']} (speed-weighted)")
            check(sum(dana["reveal"]["counts"]) == 4, f"reveal shows how the room answered: {dana['reveal']['counts']}")
            late = await client.post(f"/api/live/{code}/answer", json={"choiceIndex": right}, headers=players["Omar Haddad"])
            check(late.status_code == 409, "no changing your answer after the reveal")
            board = (await client.post(f"/api/live/{code}/control", json={"action": "next"}, headers=host)).json()
            check(board["status"] == "scoreboard" and board["standings"][0]["score"] >= board["standings"][-1]["score"],
                  f"scoreboard: {[(s['name'], s['score']) for s in board['standings']]}")
            final = (await client.post(f"/api/live/{code}/control", json={"action": "end"}, headers=host)).json()
            check(final["status"] == "finished" and "roomMissed" in final, f"finished; room recap has {len(final['roomMissed'])} item(s)")
            check(len(final.get("afterParty", {}).get("tracks", [])) >= 5, "the big screen gets a playlist for the finale")
            phone = (await client.get(f"/api/live/{code}/state", headers=players["Dana Reyes"])).json()
            check("afterParty" not in phone, "phones are not sent the finale playlist")

            # Leave the database as it was found: the throw-away players and what they played are removed.
            from scripts.reset_play_data import clear
            removed = await clear(test_only=True, apply=True)
            print("== tidy up ==\n       removed " + ", ".join(f"{n} {what}" for what, n in removed.items()))

    print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILED:\n  - " + "\n  - ".join(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
