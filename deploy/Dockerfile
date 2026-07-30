# Multi-stage build: compile the Angular app, then ship it inside the FastAPI
# image so a single container serves both the API and the static SPA. This is
# the image deployed to SciLifeLab Serve (Serve provides ingress + TLS).

# --- stage 1: build the Angular frontend ---
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build -- --configuration production

# --- stage 2: the Python backend + built frontend ---
FROM python:3.11-slim AS backend
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# SciLifeLab Serve requires containers to run as non-root, with uid 1000 in
# their documented example. Dependencies are installed as root, then the app
# directory is handed to the unprivileged user we switch to below.
RUN useradd -m -u 1000 appuser

COPY backend/pyproject.toml ./
RUN pip install .

COPY backend/ ./
# Angular's application builder emits into dist/frontend/browser.
COPY --from=frontend /frontend/dist/frontend/browser ./static

# Writable by the runtime user: the SQLite fallback lands here when no
# persistent volume is mounted (ephemeral, but the app still starts).
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000
# --proxy-headers/--forwarded-allow-ips: the app sits behind the platform's
# reverse proxy (SciLifeLab Serve terminates TLS), and it derives its own
# public URL for the QR code from the forwarded headers when PUBLIC_BASE_URL
# is not set. Only the platform's proxy can reach the container, so trusting
# the forwarded headers here is safe.
# Alembic runs migrations on start; then launch the ASGI server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
