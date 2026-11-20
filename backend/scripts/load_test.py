"""How does it hold up with many people playing at once?

    cd backend && .venv/bin/python -m scripts.load_test --players 100            # solo games
    cd backend && .venv/bin/python -m scripts.load_test --live 150               # one live game, 150 phones

Plays real games over HTTP against a running server (default http://127.0.0.1:8100) with
throw-away players, and reports how long requests took. The talking host is not exercised:
his voice comes from the AI provider, whose own limits apply (see the README).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = {}
for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        ENV[key.strip()] = value.strip().strip('"')

timings: dict[str, list[float]] = defaultdict(list)
errors: dict[str, int] = defaultdict(int)


async def call(client: httpx.AsyncClient, kind: str, method: str, url: str, **kwargs) -> httpx.Response | None:
    started = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        errors[f"{kind}: {type(exc).__name__}"] += 1
        return None
    timings[kind].append((time.perf_counter() - started) * 1000)
    if response.status_code >= 400:
        errors[f"{kind}: HTTP {response.status_code}"] += 1
        return None
    return response


async def solo_player(client: httpx.AsyncClient, slug: str, mode: str, think: float, number: int) -> bool:
    tag = uuid.uuid4().hex[:8]
    r = await call(client, "join", "POST", f"/api/quiz/{slug}/join",
                   json={"name": f"Load Tester {number}", "email": f"load.{tag}@example.com", "team": "Sales"})
    if r is None:
        return False
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = await call(client, "start", "POST", f"/api/quiz/{slug}/runs", json={"mode": mode, "decade": "80s"}, headers=headers)
    if r is None:
        return False
    run_id = r.json()["state"]["runId"]
    for _ in range(120):
        r = await call(client, "next", "POST", f"/api/runs/{run_id}/next", headers=headers)
        if r is None:
            return False
        turn = r.json()
        if turn["finished"] or not turn["item"]:
            break
        item = turn["item"]
        if item.get("sealed"):
            await asyncio.sleep(think)
            r = await call(client, "open", "POST", f"/api/runs/{run_id}/open", json={"itemId": item["id"]}, headers=headers)
            if r is None:
                return False
            item = r.json()["item"]
        if item.get("needsWager"):
            r = await call(client, "wager", "POST", f"/api/runs/{run_id}/wager",
                           json={"itemId": item["id"], "amount": item["wagerOptions"][0]}, headers=headers)
            if r is None:
                return False
            item = r.json()["item"]
        await asyncio.sleep(think * random.uniform(0.5, 1.5))
        r = await call(client, "answer", "POST", f"/api/runs/{run_id}/answer",
                       json={"itemId": item["id"], "choiceIndex": random.randrange(len(item["options"]))}, headers=headers)
        if r is None:
            return False
        if r.json()["finished"]:
            break
        await asyncio.sleep(think * random.uniform(0.3, 1.0))
    r = await call(client, "summary", "GET", f"/api/runs/{run_id}/summary", headers=headers)
    if r is None:                      # a game cut short (sudden death, the clock) is closed by quitting
        r = await call(client, "summary", "POST", f"/api/runs/{run_id}/quit", headers=headers)
    await call(client, "leaderboard", "GET", f"/api/quiz/{slug}/leaderboard?mode={mode}", headers=headers)
    return r is not None


async def run_solo(args) -> None:
    limits = httpx.Limits(max_connections=args.players + 20, max_keepalive_connections=args.players + 20)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        started = time.perf_counter()

        async def staggered(number: int) -> bool:
            await asyncio.sleep(number * args.ramp)
            return await solo_player(client, args.quiz, args.mode, args.think, number)

        results = await asyncio.gather(*(staggered(n) for n in range(args.players)))
        report(f"{args.players} players each playing a whole '{args.mode}' game at once", time.perf_counter() - started,
               f"{sum(results)} of {args.players} games finished")


async def live_phone(client: httpx.AsyncClient, code: str, number: int, delays: list[float]) -> None:
    tag = uuid.uuid4().hex[:8]
    r = await call(client, "live join", "POST", f"/api/live/{code}/join",
                   json={"name": f"Phone {number}", "email": f"phone.{tag}@example.com", "team": "Sales"})
    if r is None:
        return
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    answered = -1
    try:
        async with client.stream("GET", f"/api/live/{code}/events", headers=headers, timeout=None) as stream:
            async for line in stream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                state = json.loads(line[6:])
                if state["status"] == "finished":
                    return
                question = state.get("question")
                if state["status"] == "question" and question and state["index"] != answered:
                    opens = time.mktime(time.strptime(question["opensAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                    wait = opens - time.time()
                    if wait > 2:                       # still locked: the next update will tell us when it opens
                        continue
                    answered = state["index"]
                    await asyncio.sleep(max(0, wait) + random.uniform(0.2, 2.5))
                    started = time.perf_counter()
                    r = await call(client, "live answer", "POST", f"/api/live/{code}/answer",
                                   json={"choiceIndex": random.randrange(len(question["options"]))}, headers=headers)
                    if r is not None:
                        delays.append((time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        errors[f"live stream: {type(exc).__name__}"] += 1


async def run_live(args) -> None:
    limits = httpx.Limits(max_connections=args.live * 2 + 50, max_keepalive_connections=args.live * 2 + 50)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        r = await client.post("/api/admin/login", json={"email": ENV["ADMIN_EMAIL"], "password": ENV["ADMIN_PASSWORD"]})
        r.raise_for_status()
        admin = {"Authorization": f"Bearer {r.json()['token']}"}
        quizzes = (await client.get("/api/admin/quizzes", headers=admin)).json()["quizzes"]
        quiz_id = next(q["id"] for q in quizzes if q["slug"] == args.quiz)
        session = (await client.post(f"/api/admin/quizzes/{quiz_id}/live", headers=admin, json={"decade": "80s"})).json()
        code, host = session["code"], {"X-Host-Key": session["hostKey"]}
        delays: list[float] = []
        started = time.perf_counter()
        phones = [asyncio.create_task(live_phone(client, code, n, delays)) for n in range(args.live)]
        for _ in range(120):                                   # wait for everyone to be in the lobby
            await asyncio.sleep(0.5)
            lobby = (await client.get(f"/api/live/{code}/state", headers=host)).json()
            if lobby["players"] >= args.live:
                break
        print(f"  {lobby['players']} phones joined in {time.perf_counter() - started:.1f}s", flush=True)
        reveal_waits = []
        for question in range(session["questions"]):
            await call(client, "live control", "POST", f"/api/live/{code}/control", json={"action": "next"}, headers=host)
            await asyncio.sleep(1.0)                           # the host "reads the question"
            await call(client, "live control", "POST", f"/api/live/{code}/control", json={"action": "open"}, headers=host)
            opened = time.perf_counter()
            for _ in range(200):                               # the question closes itself when everyone has answered
                await asyncio.sleep(0.25)
                r = await call(client, "live state (big screen)", "GET", f"/api/live/{code}/state", headers=host)
                if r is not None and r.json()["status"] == "reveal":
                    break
            reveal_waits.append(time.perf_counter() - opened)
            await call(client, "live control", "POST", f"/api/live/{code}/control", json={"action": "next"}, headers=host)   # scoreboard
            await asyncio.sleep(0.5)
        final = await call(client, "live control", "POST", f"/api/live/{code}/control", json={"action": "end"}, headers=host)
        await asyncio.wait(phones, timeout=20)
        answers = final.json()["standings"] if final is not None else []
        report(f"one live game: {args.live} phones, {session['questions']} questions", time.perf_counter() - started,
               f"{len(delays)} answers delivered of {args.live * session['questions']} possible; a question closed "
               f"{statistics.median(reveal_waits):.1f}s after opening (median) once everyone had answered; "
               f"top score {answers[0]['score'] if answers else 'n/a'}")


def report(title: str, seconds: float, outcome: str) -> None:
    print(f"\n{title}\n  {outcome}\n  took {seconds:.1f}s; {sum(len(v) for v in timings.values())} requests, "
          f"{sum(len(v) for v in timings.values()) / seconds:.0f} per second")
    print(f"  {'request':26s} {'count':>6s} {'typical':>9s} {'slowest 5%':>11s} {'worst':>8s}")
    for kind, values in sorted(timings.items()):
        values.sort()
        print(f"  {kind:26s} {len(values):6d} {statistics.median(values):7.0f}ms {values[int(len(values) * 0.95) - 1]:9.0f}ms {values[-1]:6.0f}ms")
    print("  errors:", dict(errors) or "none")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--quiz", default="q3-review")
    parser.add_argument("--players", type=int, default=50, help="simultaneous solo players")
    parser.add_argument("--mode", default="show", choices=["show", "sudden_death", "speed", "straight"])
    parser.add_argument("--think", type=float, default=0.5, help="seconds a player takes over each step")
    parser.add_argument("--ramp", type=float, default=0.03, help="seconds between one player starting and the next")
    parser.add_argument("--live", type=int, default=0, help="instead: one live game with this many phones")
    parser.add_argument("--keep", action="store_true", help="leave the throw-away players in the database afterwards")
    args = parser.parse_args()
    asyncio.run(run_live(args) if args.live else run_solo(args))
    if not args.keep:
        asyncio.run(tidy_up())


async def tidy_up() -> None:
    """Remove the throw-away players and put back any question their random answers got pulled."""
    import db
    from scripts.reset_play_data import clear

    await db.init_pool()
    removed = await clear(test_only=True, apply=True)
    await db.close_pool()
    print("  tidied up: " + ", ".join(f"{n} {what}" for what, n in removed.items()))


if __name__ == "__main__":
    main()
