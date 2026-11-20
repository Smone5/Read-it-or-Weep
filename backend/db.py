"""PostgreSQL access (asyncpg) and a tiny migration runner.

Rows come back as plain dicts with UUIDs as strings, so the rest of the code can
compare and serialise ids without caring about driver types.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import asyncpg

from config import BACKEND_DIR, config

log = logging.getLogger("db")

MIGRATIONS_DIR = BACKEND_DIR / "migrations"
_MIGRATION_LOCK_ID = 727_001  # arbitrary; serialises migrations across app instances

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    for name in ("json", "jsonb"):
        await conn.set_type_codec(name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: _plain(value) for key, value in record.items()}


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        config.validate()
        _pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=12, init=_init_connection)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised.")
    return _pool


async def fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [_row(r) for r in await pool().fetch(sql, *args)]  # type: ignore[misc]


async def fetchrow(sql: str, *args: Any) -> dict[str, Any] | None:
    return _row(await pool().fetchrow(sql, *args))


async def fetchval(sql: str, *args: Any) -> Any:
    return _plain(await pool().fetchval(sql, *args))


async def execute(sql: str, *args: Any) -> str:
    return await pool().execute(sql, *args)


async def executemany(sql: str, rows: list[tuple]) -> None:
    if rows:
        await pool().executemany(sql, rows)


class Tx:
    """Same helpers as the module, bound to one connection inside a transaction."""

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return [_row(r) for r in await self.conn.fetch(sql, *args)]  # type: ignore[misc]

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return _row(await self.conn.fetchrow(sql, *args))

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return _plain(await self.conn.fetchval(sql, *args))

    async def execute(self, sql: str, *args: Any) -> str:
        return await self.conn.execute(sql, *args)

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        if rows:
            await self.conn.executemany(sql, rows)


@asynccontextmanager
async def transaction() -> AsyncIterator[Tx]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            yield Tx(conn)


async def run_migrations(directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply any ``NNN_name.sql`` files not yet recorded. Safe to call from every instance."""
    applied_now: list[str] = []
    async with pool().acquire() as conn:
        await conn.execute("select pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                "create table if not exists schema_migrations ("
                " version text primary key, applied_at timestamptz not null default now())"
            )
            done = {r["version"] for r in await conn.fetch("select version from schema_migrations")}
            for path in sorted(directory.glob("*.sql")):
                if path.stem in done:
                    continue
                log.info("applying migration %s", path.name)
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute("insert into schema_migrations (version) values ($1)", path.stem)
                applied_now.append(path.stem)
        finally:
            await conn.execute("select pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
    return applied_now
