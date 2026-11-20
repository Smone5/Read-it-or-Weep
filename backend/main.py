"""Application entry point.

One process serves the JSON API, the host voice channel, the live-game stream and
(when built) the web app's static files – so a deployment is a single container
plus a PostgreSQL database, on any cloud.

    uvicorn main:app --host 0.0.0.0 --port 8100
"""
from __future__ import annotations

import logging
import os
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
    password = config.bootstrap_admin_password
    if config.bootstrap_admin_email and password and (len(password) < 10 or password.lower().startswith("change-me")):
        log.warning("ADMIN_PASSWORD is too short or still the example value – no admin was created. "
                    "Set a real one (10+ characters), or run scripts.create_admin.")
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


# The interactive API pages are for development; they are off unless API_DOCS=1.
_docs = os.environ.get("API_DOCS") == "1"
app = FastAPI(title="Read It or Weep", version="1.0.0", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None)

# What a page from this site may load, and from where. Scripts only from the site itself; pictures
# and sound also from Apple (album covers and 30-second clips); nothing may put the site in a frame.
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https://*.mzstatic.com; "
        "media-src 'self' data: blob: https://*.itunes.apple.com https://*.mzstatic.com; "
        "font-src 'self' data:; connect-src 'self' ws: wss: https://*.itunes.apple.com https://*.mzstatic.com; worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
_SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"x-frame-options": b"DENY",
    b"permissions-policy": b"microphone=(self), camera=(), geolocation=()",
    b"content-security-policy": _CSP.encode(),
}


class SecurityHeaders:
    """Adds the standard protective headers to every response (plain ASGI, so streams are untouched)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        secure = scope.get("scheme") == "https"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in _SECURITY_HEADERS.items() if name not in present]
                if secure and b"strict-transport-security" not in present:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": headers}
            await send(message)

        await self.inner(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)

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
