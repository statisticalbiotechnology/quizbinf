#!/bin/sh
# Serve the built Angular app from the backend, the way the production image
# does, so the smoke test exercises the same single-origin setup as a
# deployment (relative /api URLs, SPA fallback routing, SSE on one host).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
FRONTEND=$(dirname "$HERE")
BACKEND=$(dirname "$FRONTEND")/backend

if [ ! -d "$FRONTEND/dist/frontend/browser" ]; then
    echo "build the frontend first: npm run build -- --configuration production" >&2
    exit 1
fi

rm -rf "$BACKEND/static"
cp -r "$FRONTEND/dist/frontend/browser" "$BACKEND/static"

# Throwaway state; never touch a real data directory.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Prefer the project venv, fall back to whatever is on PATH (CI installs the
# backend into the job's Python).
if [ -x "$BACKEND/.venv/bin/uvicorn" ]; then
    UVICORN="$BACKEND/.venv/bin/uvicorn"
else
    UVICORN=uvicorn
fi

if [ -x "$BACKEND/.venv/bin/python" ]; then
    PYTHON="$BACKEND/.venv/bin/python"
else
    PYTHON=python3
fi

cd "$BACKEND"
export DATABASE_URL="sqlite:///$WORK/e2e.db"
export DATA_DIR="$WORK"
export MOCK_LOGIN=true
export ROSTER_LOGIN=true
export ROSTER_TEACHER_PASSWORD=e2e-teacher-password
export ENVIRONMENT=development
export TEACHER_USERNAMES=teacher
export SESSION_SECRET=e2e-only-secret

# There is no Canvas here, so put a small roster in directly. Without it the
# roster login and its type-ahead would have nothing to match against.
"$PYTHON" "$HERE/seed_roster.py"

exec "$UVICORN" app.main:app --host 127.0.0.1 --port 8020 \
    --proxy-headers --forwarded-allow-ips='*'
