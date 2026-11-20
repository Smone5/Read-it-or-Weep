"""Clean up pictures pulled out of a report so they are safe and useful to show in a game.

Drops logos, icons, rules and blank fillers; flattens transparency onto white (charts
with transparent backgrounds are unreadable on a dark screen); caps the size.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

MAX_SIDE = 1600
MIN_WIDTH, MIN_HEIGHT, MIN_AREA = 220, 130, 60_000
MAX_ASPECT = 7.0
Image.MAX_IMAGE_PIXELS = 80_000_000  # refuse decompression bombs


@dataclass
class CleanImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str


def _vector_to_png(data: bytes, suffix: str) -> bytes | None:
    """EMF/WMF pictures need LibreOffice to rasterise. Optional: skipped when it is not installed."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"image{suffix}"
        source.write_bytes(data)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "png", "--outdir", tmp, str(source)],
                capture_output=True, timeout=45, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        output = Path(tmp) / "image.png"
        return output.read_bytes() if output.is_file() else None


def clean_image(data: bytes, mime: str | None) -> tuple[CleanImage | None, str | None]:
    """Return (image, None) when usable, or (None, reason) when it should be skipped."""
    mime = (mime or "").lower()
    if "emf" in mime or "wmf" in mime or data[:4] == b"\x01\x00\x00\x00" or data[:4] == b"\xd7\xcd\xc6\x9a":
        converted = _vector_to_png(data, ".emf" if "emf" in mime or data[:4] == b"\x01\x00\x00\x00" else ".wmf")
        if converted is None:
            return None, "vector picture (EMF/WMF) could not be converted"
        data, mime = converted, "image/png"
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, "unsupported picture format"

    was_jpeg = (image.format or "").upper() in ("JPEG", "MPO")
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, rgba).convert("RGB")
    elif image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    if width < MIN_WIDTH or height < MIN_HEIGHT or width * height < MIN_AREA:
        return None, "too small (logo or icon)"
    if max(width / height, height / width) > MAX_ASPECT:
        return None, "decorative strip"
    spread = ImageStat.Stat(image.convert("L").resize((64, 64))).stddev[0]
    if spread < 6:
        return None, "blank or near-solid picture"

    if max(width, height) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        width, height = image.size

    out = io.BytesIO()
    if was_jpeg:
        image.save(out, format="JPEG", quality=86, optimize=True)
        out_mime = "image/jpeg"
    else:
        image.save(out, format="PNG", optimize=True)
        out_mime = "image/png"
    payload = out.getvalue()
    return CleanImage(payload, out_mime, width, height, hashlib.sha256(payload).hexdigest()), None
