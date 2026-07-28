from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from .config import get_settings
from .db import Base, engine
from .routers import auth, quizzes, sessions

# Built Angular app; present in the production image, absent in development.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic owns the schema in production; create_all covers dev/tests.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="quizbinf", lifespan=lifespan)

settings = get_settings()
if settings.environment != "production":
    # ng serve runs on :4200 during development; cookies need credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(quizzes.router)
app.include_router(sessions.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # Serve real files (JS/CSS bundles) directly, everything else gets
        # index.html so Angular's router handles /s/<code> etc.
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
