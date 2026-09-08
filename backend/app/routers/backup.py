"""Downloading a copy of the volume.

Teacher-only, like every other view of student data — the archive contains
names and answers, so it is at least as sensitive as the Participants view.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from .. import backup
from ..auth import current_teacher
from ..config import Settings, get_settings
from ..models import User

router = APIRouter(prefix="/api", tags=["backup"])


@router.get("/backup.zip", include_in_schema=False)
def download_backup(
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """The database, the uploaded figures and the configuration, as one file.

    Built into a temporary directory and streamed from there, rather than
    assembled in memory: the figures alone can run to tens of megabytes, and
    the process serving a lecture should not hold that.
    """
    workspace = Path(tempfile.mkdtemp(prefix="quizbinf-backup-"))
    try:
        archive = backup.build(settings, workspace)
    except backup.NotSupported as e:
        _clean(workspace)
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e))
    except OSError as e:
        _clean(workspace)
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            f"Could not write the backup: {e}",
        )

    return FileResponse(
        archive,
        media_type="application/zip",
        filename=archive.name,
        # The temporary copy goes as soon as the response has been sent.
        background=BackgroundTask(_clean, workspace),
        headers={"Cache-Control": "no-store"},
    )


def _clean(workspace: Path) -> None:
    import shutil

    shutil.rmtree(workspace, ignore_errors=True)
