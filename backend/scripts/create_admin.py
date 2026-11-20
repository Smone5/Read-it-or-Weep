"""Create (or reset the password of) an admin account.

    cd backend && .venv/bin/python -m scripts.create_admin you@company.com

The password is asked for without echoing, or generated for you with --generate.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from security import hash_password  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--name", default=None)
    parser.add_argument("--generate", action="store_true", help="generate a strong password and print it once")
    args = parser.parse_args()

    if args.generate:
        password = secrets.token_urlsafe(14)
    else:
        password = getpass.getpass("Password (10+ characters): ")
        if len(password) < 10 or password != getpass.getpass("Again: "):
            sys.exit("Passwords must match and be at least 10 characters.")

    await db.init_pool()
    await db.run_migrations()
    await db.execute(
        "insert into admin_users (email, name, password_hash) values ($1, $2, $3)"
        " on conflict (lower(email)) do update set password_hash = excluded.password_hash,"
        " name = coalesce(excluded.name, admin_users.name)",
        args.email.lower(), args.name, hash_password(password))
    await db.close_pool()
    print(f"Admin ready: {args.email}")
    if args.generate:
        print(f"Password (shown once): {password}")


if __name__ == "__main__":
    asyncio.run(main())
