#!/bin/sh
# SciLifeLab Serve starts a container by running ./start-script.sh from the
# working directory rather than the image's CMD, so this file must exist at
# /app/start-script.sh and be executable. Without it the deployment fails with
#   /bin/sh: 1: ./start-script.sh: not found
# and no application log, because nothing ever starts.
set -eu

# Alembic owns the schema in deployment; run migrations before serving.
alembic upgrade head

# exec so uvicorn becomes PID 1 and receives SIGTERM directly on shutdown.
# --proxy-headers/--forwarded-allow-ips: the platform terminates TLS in front
# of us, and the app derives its own public URL (for the QR code) from the
# forwarded headers when PUBLIC_BASE_URL is not set.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips="*"
