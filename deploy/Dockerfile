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

COPY backend/pyproject.toml ./
RUN pip install .

COPY backend/ ./
# Angular's application builder emits into dist/frontend/browser.
COPY --from=frontend /frontend/dist/frontend/browser ./static

EXPOSE 8000
# Alembic runs migrations on start; then launch the ASGI server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
