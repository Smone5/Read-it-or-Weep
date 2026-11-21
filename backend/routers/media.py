"""Serving report pictures and the company logo through the backend.

Pictures are reached only by short-lived signed links, and the blurred "from
memory" version is produced here on the server, so a curious player cannot fetch
the sharp picture early by inspecting the page.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageFilter, UnidentifiedImageError

import db
from security import verify_media
from services import settings_store
from storage import LocalStorage, storage

router = APIRouter(prefix="/api/media", tags=["media"])

_blur_cache: OrderedDict[str, bytes] = OrderedDict()


def _blurred(data: bytes) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((520, 520))
    image = image.filter(ImageFilter.GaussianBlur(radius=max(9, image.width // 26)))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=70)
    return out.getvalue()


@router.get("/image/{image_id}")
async def report_image(image_id: uuid.UUID, v: str = "sharp", exp: int = 0, sig: str = "") -> Response:
    sid = str(image_id)
    if v not in ("sharp", "blur") or not verify_media(f"{sid}:{v}", exp, sig):
        raise HTTPException(403, "This picture link has expired.")
    row = await db.fetchrow("select storage_key, mime_type from doc_images where id = $1", sid)
    if row is None:
        raise HTTPException(404, "Picture not found.")
    headers = {"Cache-Control": "private, max-age=3600", "Content-Security-Policy": "default-src 'none'"}
    if v == "blur":
        if sid not in _blur_cache:
            try:
                raw = await storage().get(row["storage_key"])
                _blur_cache[sid] = await asyncio.to_thread(_blurred, raw)
            except FileNotFoundError as exc:
                raise HTTPException(404, "Picture not found.") from exc
            except (UnidentifiedImageError, OSError) as exc:
                raise HTTPException(422, "Picture could not be processed.") from exc
            while len(_blur_cache) > 200:
                _blur_cache.popitem(last=False)
        return Response(_blur_cache[sid], media_type="image/jpeg", headers=headers)
    try:
        data = await storage().get(row["storage_key"])
    except FileNotFoundError as exc:
        raise HTTPException(404, "Picture not found.") from exc
    return Response(data, media_type=row["mime_type"], headers=headers)


@router.get("/document/{document_id}")
async def report_document(document_id: uuid.UUID, exp: int = 0, sig: str = "", admin: int = 0) -> Response:
    """Serve an uploaded report file only when the quiz is open (or admin-signed), allows downloads, and signature is valid."""
    sid = str(document_id)
    is_admin_link = bool(admin) and verify_media(f"doc:{sid}:admin", exp, sig)
    if not is_admin_link and not verify_media(f"doc:{sid}", exp, sig):
        raise HTTPException(403, "This report link has expired. Refresh the page for a new one.")
    row = await db.fetchrow(
        "select d.filename, d.storage_key, d.mime_type, q.id as quiz_id, q.status, q.settings"
        " from documents d join quizzes q on q.id = d.quiz_id where d.id = $1", sid)
    if row is None or not row["storage_key"]:
        raise HTTPException(404, "Report file is not available for download.")
    if row["status"] != "open" and not is_admin_link:
        raise HTTPException(403, "This quiz is no longer open for report downloads.")
    rules = await settings_store.quiz_rules({"settings": row["settings"]})
    if not rules.get("allow_download"):
        raise HTTPException(403, "Report download is switched off for this quiz.")

    safe_name = row["filename"].replace('"', "").replace("\r", "").replace("\n", "").replace("/", "_").replace("\\", "_")
    headers = {
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    media_type = row["mime_type"] or "application/octet-stream"
    store = storage()
    if isinstance(store, LocalStorage):
        try:
            path = store._path(row["storage_key"])
        except ValueError as exc:
            raise HTTPException(404, "Report file is no longer stored on this server.") from exc
        if not path.is_file():
            raise HTTPException(404, "Report file is no longer stored on this server.")
        return FileResponse(path, media_type=media_type, headers=headers)
    try:
        data = await store.get(row["storage_key"])
    except FileNotFoundError as exc:
        raise HTTPException(404, "Report file is no longer stored on this server.") from exc

    def _chunks():
        view = memoryview(data)
        for start in range(0, len(view), 65_536):
            yield bytes(view[start:start + 65_536])

    return StreamingResponse(_chunks(), media_type=media_type, headers=headers)


@router.get("/logo")
async def logo() -> Response:
    branding = await settings_store.get("branding")
    if not branding.get("logo_key"):
        raise HTTPException(404, "No logo has been uploaded.")
    try:
        data = await storage().get(branding["logo_key"])
    except FileNotFoundError as exc:
        raise HTTPException(404, "No logo has been uploaded.") from exc
    mime = "image/svg+xml" if branding["logo_key"].endswith(".svg") else (
        "image/jpeg" if branding["logo_key"].endswith((".jpg", ".jpeg")) else "image/png")
    return Response(data, media_type=mime, headers={"Cache-Control": "public, max-age=300",
                                                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
