"""Serving report pictures and the company logo through the backend.

Pictures are reached only by short-lived signed links, and the blurred "from
memory" version is produced here on the server, so a curious player cannot fetch
the sharp picture early by inspecting the page.
"""
from __future__ import annotations

import io
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Response
from PIL import Image, ImageFilter

import db
from security import verify_media
from services import settings_store
from storage import storage

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
async def report_image(image_id: str, v: str = "sharp", exp: int = 0, sig: str = "") -> Response:
    if v not in ("sharp", "blur") or not verify_media(f"{image_id}:{v}", exp, sig):
        raise HTTPException(403, "This picture link has expired.")
    row = await db.fetchrow("select storage_key, mime_type from doc_images where id = $1", image_id)
    if row is None:
        raise HTTPException(404, "Picture not found.")
    headers = {"Cache-Control": "private, max-age=3600"}
    if v == "blur":
        if image_id not in _blur_cache:
            _blur_cache[image_id] = _blurred(await storage().get(row["storage_key"]))
            while len(_blur_cache) > 200:
                _blur_cache.popitem(last=False)
        return Response(_blur_cache[image_id], media_type="image/jpeg", headers=headers)
    try:
        data = await storage().get(row["storage_key"])
    except FileNotFoundError as exc:
        raise HTTPException(404, "Picture not found.") from exc
    return Response(data, media_type=row["mime_type"], headers=headers)


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
