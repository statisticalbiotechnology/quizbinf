"""Preview rendering for the authoring form.

Rendered by the server rather than in the browser so the preview goes through
exactly the same renderer and sanitiser as the question students will see —
a preview that disagrees with the real thing is worse than none.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import current_teacher
from ..markdown import render
from ..models import User

router = APIRouter(prefix="/api/markdown", tags=["markdown"])


class PreviewIn(BaseModel):
    text: str = Field(max_length=20_000)


@router.post("/preview")
def preview(body: PreviewIn, teacher: User = Depends(current_teacher)) -> dict:
    return {"html": render(body.text)}
