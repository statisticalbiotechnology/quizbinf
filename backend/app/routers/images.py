"""Uploading figures for use in question Markdown.

Files live on the mounted volume next to the database, not in the container,
so they survive a redeploy. Served back from the app itself, which keeps a
question self-contained: no dependency on an external host still being up in
the middle of a lecture.
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.responses import FileResponse

from ..auth import current_teacher
from ..config import Settings, get_settings
from ..models import User

router = APIRouter(prefix="/api/images", tags=["images"])

# Enough for a figure from a paper; small enough that a stray upload cannot
# fill the volume. Serve's default is 1 GB shared with the database.
MAX_BYTES = 4 * 1024 * 1024

# Only formats a browser renders inline. Deliberately no SVG: it can carry
# script, and it would be served from our own origin.
ALLOWED = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Magic bytes, checked against the declared type — a content-type header is
# whatever the client says it is.
_SIGNATURES = {
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],
}


def _images_dir(settings: Settings) -> Path:
    path = Path(settings.data_dir) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Store a figure and return the Markdown to paste into a question."""
    suffix = ALLOWED.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Use a PNG, JPEG, GIF or WebP image",
        )

    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Images must be smaller than {MAX_BYTES // (1024 * 1024)} MB",
        )
    if not any(data.startswith(sig) for sig in _SIGNATURES[suffix]):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "That file does not look like the image type it claims to be",
        )

    try:
        directory = _images_dir(settings)
    except OSError:
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            "No writable storage is configured for uploads",
        )

    # Random name: the original filename is attacker-controlled, and a
    # predictable one would let anyone guess at other teachers' figures.
    name = f"{secrets.token_urlsafe(16)}{suffix}"
    (directory / name).write_bytes(data)
    url = f"/api/images/{name}"
    return {"url": url, "markdown": f"![]({url})"}


@router.get("/{name}", include_in_schema=False)
def get_image(name: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    """Serve an uploaded figure.

    Not teacher-only: students must see the figure in the question. Names are
    unguessable, which is the only thing keeping them unlisted.
    """
    # Reject anything that is not a bare generated filename, so no request can
    # walk out of the images directory.
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    suffix = Path(name).suffix
    if suffix not in _SIGNATURES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    path = (_images_dir(settings) / name).resolve()
    images_root = _images_dir(settings).resolve()
    if images_root not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(path)
