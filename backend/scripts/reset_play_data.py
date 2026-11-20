"""Clear out play history – before inviting real testers, or after a load test.

    cd backend && .venv/bin/python -m scripts.reset_play_data --test-players          # show what would go
    cd backend && .venv/bin/python -m scripts.reset_play_data --test-players --yes    # do it
    cd backend && .venv/bin/python -m scripts.reset_play_data --all --yes             # every player and game

``--test-players`` removes only throw-away players (addresses at example.com, which no real
person can have) and everything they played. ``--all`` removes every player, game and live
session. Quizzes, reports, questions, music and settings are never touched; question statistics
are recalculated from whatever play history is left.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

TEST_PLAYERS = "email like '%@example.com'"


async def clear(test_only: bool, apply: bool) -> dict[str, int]:
    who = TEST_PLAYERS if test_only else "true"
    counts = {
        "players": await db.fetchval(f"select count(*) from players where {who}"),
        "games": await db.fetchval(f"select count(*) from runs where player_id in (select id from players where {who})"),
        "live games": await db.fetchval(
            "select count(*) from live_sessions s where not exists (select 1 from live_participants p"
            f" join players pl on pl.id = p.player_id where p.session_id = s.id and not ({who}))"),
    }
    if not apply:
        return counts
    async with db.transaction() as connection:
        await connection.execute(
            "delete from live_sessions s where not exists (select 1 from live_participants p"
            f" join players pl on pl.id = p.player_id where p.session_id = s.id and not ({who}))")
        await connection.execute(f"delete from players where {who}")        # their games go with them
        await connection.execute("delete from question_flags where player_id is null")   # ...and their complaints
        # Statistics and automatic "pulled for review" marks were shaped by the history just removed.
        await connection.execute(
            "update questions q set"
            " times_served = (select count(*) from run_items i where i.question_id = q.id and i.answered_at is not null),"
            " times_correct = (select count(*) from run_items i where i.question_id = q.id and i.is_correct),"
            " flag_count = (select count(*) from question_flags f where f.question_id = q.id)")
        restored = await connection.execute(
            "update questions set status = 'approved', quality = quality - 'pulled'"
            " where status = 'pending' and quality->>'pulled' = 'almost nobody answers it correctly'")
        counts["questions put back in play"] = int(restored.split()[-1])
        await connection.execute(
            "update players p set games_played_count = (select count(*) from runs r where r.player_id = p.id"
            " and r.status = 'completed')")
    return counts


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test-players", action="store_true", help="only throw-away players (addresses at example.com)")
    group.add_argument("--all", action="store_true", help="every player, game and live session")
    parser.add_argument("--yes", action="store_true", help="actually delete (without this, only show the numbers)")
    args = parser.parse_args()
    await db.init_pool()
    counts = await clear(args.test_players, args.yes)
    print(("Removed: " if args.yes else "Would remove: ") + ", ".join(f"{n} {what}" for what, n in counts.items()))
    if not args.yes:
        print("Nothing was changed. Add --yes to do it.")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
