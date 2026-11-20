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
    sealed_rounds: list[str] = []
    guard = 0
    while not turn["finished"] and guard < 140:
        guard += 1
        item = turn["item"]
        blob = json.dumps(item)
        leaked = leaked or "correctIndex" in blob or "correct_index" in blob
        if item.get("sealed"):
            # The first item of a round arrives sealed: the browser shows its title card, then opens it.
            sealed_rounds.append("Music Break" if item["slot"] == "music" else item["round"]["name"])
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
            r = await client.post(f"/api/runs/{run_id}/wager", json={"itemId": item["id"], "amount": amount}, headers=headers)
            assert r.status_code == 200, r.text
            item = r.json()["item"]
            check(item["text"] and item["wager"] == amount, f"{label}: wager of {amount} accepted and question revealed")
        slots.append(item["slot"])
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
                    if kind == "fifty":
                        check(len(got["removed"]) == 2 and right not in got["removed"], f"{label}: 50/50 removes two wrong answers")
                    elif kind == "peek":
                        check(bool(got["peek"]), f"{label}: peek returns the passage ({len(got['peek'] or '')} chars)")
                    else:
                        check(bool(got["hint"]), f"{label}: hint returned: {got['hint'][:70]!r}")
                    check(got["multiplier"] < 1, f"{label}: {kind} reduces the points on offer (x{got['multiplier']})")
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
        score_seen = reveal["state"]["score"]
        if guard == 1:
            again = await client.post(f"/api/runs/{run_id}/answer", json={"itemId": item["id"], "choiceIndex": right}, headers=headers)
            check(again.json()["selectedIndex"] == choice, f"{label}: a second answer to the same question is ignored")
        if reveal["finished"]:
            break
        turn = (await client.post(f"/api/runs/{run_id}/next", headers=headers)).json()
    summary = (await client.get(f"/api/runs/{run_id}/summary", headers=headers)).json()["summary"]
    check(not leaked, f"{label}: no answer key in anything sent before answering")
    check(summary["score"] == score_seen, f"{label}: final score {summary['score']} matches the running score")
    print(f"       {label}: {len(slots)} items, rounds={rounds}, score={summary['score']}, accuracy={summary['accuracy']}%, "
          f"badge={summary['badge']!r}, readiness={summary['readiness']['cleared']}/{summary['readiness']['total']}")
    return {"summary": summary, "slots": slots, "rounds": rounds, "run_id": run_id, "sealed": sealed_rounds}


async def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "q3-review"
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
            print("== public ==")
            check((await client.get("/api/health")).json() == {"ok": True}, "health")
            info = (await client.get(f"/api/quiz/{slug}")).json()
            check(info["quiz"]["ready"], f"quiz '{info['quiz']['title']}' is ready with {info['quiz']['questions']} questions")

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
                "settings": {"modes": ["show", "sudden_death", "speed", "straight"]}, "teams": ["Sales", "Operations"]})
            check(r.status_code == 200 and len(r.json()["rules"]["modes"]) == 4, "all four styles of play switched on")

            print("== joining ==")
            tag = uuid.uuid4().hex[:6]
            r = await client.post(f"/api/quiz/{slug}/join", json={"name": "Dana Reyes", "email": f"dana.{tag}@example.com"})
            check(r.status_code == 422, "joining without a team is refused when teams are set")
            players = {}
            for name, team in (("Dana Reyes", "Sales"), ("Omar Haddad", "Operations"), ("Li Wei", "Sales")):
                r = await client.post(f"/api/quiz/{slug}/join", json={
                    "name": name, "email": f"{name.split()[0].lower()}.{tag}@example.com", "team": team})
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
            results = (await client.get(f"/api/admin/quizzes/{quiz_id}/results", headers=admin)).json()
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
