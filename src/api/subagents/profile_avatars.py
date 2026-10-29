"""Uploaded profile pictures for agent profiles.

Images are normalized (square center-crop, max 256 px, PNG) and stored under
``<home>/output/avatars/<user>/<sha>.png``, served by the existing ``/output/``
route. The returned ``/output/…`` URL is what a profile's ``avatar`` holds; the
chat bubble renderer already accepts ``/output/`` sources. Content-addressed,
so re-uploading the same picture reuses the file. Not pruned (unlike
``output/shared``).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from core.runtime_paths import output_dir

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
SIZE = 256
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


class AvatarError(ValueError):
    """Rejected upload."""


def _sniff(data: bytes) -> str:
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise AvatarError("Use a PNG, JPEG, GIF or WebP image.")


def normalize(data: bytes) -> bytes:
    """Square, ≤256 px PNG bytes. Raises ``AvatarError`` on bad input."""
    if not data:
        raise AvatarError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarError("Images must be 8 MB or smaller.")
    _sniff(data)
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # Pillow ships in requirements; stay explicit.
        raise AvatarError("Image support (Pillow) is not installed.") from exc
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            img = ImageOps.exif_transpose(img).convert("RGBA")
            side = min(img.size)
            if side < 8:
                raise AvatarError("That image is too small.")
            left = (img.width - side) // 2
            top = (img.height - side) // 2
            img = img.crop((left, top, left + side, top + side))
            if side > SIZE:
                img = img.resize((SIZE, SIZE), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except AvatarError:
        raise
    except Exception as exc:  # noqa: BLE001 — any decoder failure is a bad upload
        raise AvatarError("That file could not be read as an image.") from exc


def avatars_root() -> Path:
    return output_dir() / "avatars"


def store(user_id: int, data: bytes) -> str:
    """Normalize and persist; return the ``/output/avatars/…`` URL."""
    png = normalize(data)
    digest = hashlib.sha256(png).hexdigest()[:20]
    folder = avatars_root() / str(int(user_id))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.png"
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(path)
    return f"/output/avatars/{int(user_id)}/{digest}.png"
