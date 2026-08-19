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

cd "$BACKEND"
DATABASE_URL="sqlite:///$WORK/e2e.db" \
DATA_DIR="$WORK" \
MOCK_LOGIN=true \
ROSTER_LOGIN=true \
ROSTER_TEACHER_PASSWORD=e2e-teacher-password \
ENVIRONMENT=development \
TEACHER_USERNAMES=teacher \
SESSION_SECRET=e2e-only-secret \
exec "$UVICORN" app.main:app --host 127.0.0.1 --port 8020 \
    --proxy-headers --forwarded-allow-ips='*'
