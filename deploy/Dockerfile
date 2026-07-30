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

# SciLifeLab Serve runs ./start-script.sh from the working directory instead
# of the image's CMD, so it must exist here and be executable.
COPY deploy/start-script.sh ./start-script.sh
RUN chmod +x ./start-script.sh

# Owned by uid 1000, group 0, with group permissions mirroring the user's.
# Group 0 matters on platforms that run containers under an arbitrary uid
# (OpenShift-style): such a uid is still a member of group 0, so it can write
# here. The SQLite fallback lands in /app when no persistent volume is mounted.
RUN chown -R 1000:0 /app && chmod -R g=u /app

# Numeric, not `USER appuser`. Kubernetes does not read /etc/passwd from the
# image, so with runAsNonRoot=true a *named* user fails admission with
# "image has non-numeric user (appuser), cannot verify user is non-root" —
# the pod never starts and no application log is produced.
USER 1000

EXPOSE 8000
# The startup sequence (migrate, then serve) lives in start-script.sh so that
# it is identical whether the platform honours ENTRYPOINT or invokes the
# script itself, as Serve does.
ENTRYPOINT ["./start-script.sh"]
