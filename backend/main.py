"""Application entry point.

One process serves the JSON API, the host voice channel, the live-game stream and
(when built) the web app's static files – so a deployment is a single container
plus a PostgreSQL database, on any cloud.

    uvicorn main:app --host 0.0.0.0 --port 8100
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
from config import config
from routers import admin, host, live, media, play
from security import hash_password
from services import music_service, settings_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")


async def _bootstrap_admin() -> None:
    """Create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD when no admin exists yet."""
    if await db.fetchval("select 1 from admin_users limit 1"):
        return
    if config.bootstrap_admin_email and config.bootstrap_admin_password:
        await db.execute("insert into admin_users (email, name, password_hash) values ($1, 'Admin', $2)",
                         config.bootstrap_admin_email.lower(), hash_password(config.bootstrap_admin_password))
        log.info("created the first admin account for %s", config.bootstrap_admin_email)
    else:
        log.warning("no admin account exists yet – set ADMIN_EMAIL and ADMIN_PASSWORD, or run scripts.create_admin")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_pool()
    applied = await db.run_migrations()
    if applied:
        log.info("applied migrations: %s", ", ".join(applied))
    await _bootstrap_admin()
    # Documents caught mid-processing by a restart would otherwise look busy forever.
    await db.execute("update documents set status = 'failed', stage = null,"
                     " error = 'Processing was interrupted. Use \"Process again\".' where status = 'processing'")
    # ...and a "write more questions" job cut short would leave a finished document looking busy.
    await db.execute("update documents set stage = null, progress = '{}'::jsonb where status = 'ready' and stage is not null")
    if not config.ai_configured:
        log.warning("no AI provider configured – uploads and the host are disabled until one is set")
    else:
        game = await settings_store.get("game")
        if game["music_enabled"] and config.prefill_music:
            music_service.prefill(game["decades"])
    yield
    await db.close_pool()


app = FastAPI(title="Read It or Weep", version="1.0.0", lifespan=lifespan)

if config.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Disposition"])


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again."}, status_code=500)


for module in (play, media, live, host, admin):
    app.include_router(module.router)


# --- the web app (present once `web/` has been built) ---------------------------
_dist = Path(config.web_dist_dir)
if (_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def web_app(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        candidate = (_dist / path).resolve()
        if path and _dist.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
