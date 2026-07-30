"""Work out the public URL students should be sent to.

The QR code is built from this, so getting it wrong means students scan a code
pointing nowhere. `PUBLIC_BASE_URL` is authoritative when set; otherwise it is
derived from the incoming request, which is what makes the app deployable on a
platform (like SciLifeLab Serve) that offers no way to set environment
variables — the app simply learns its own hostname from the first request.

Deriving it requires the reverse proxy's forwarded headers to be trusted, so
uvicorn runs with --proxy-headers (see deploy/Dockerfile).
"""

from fastapi import Request

from .config import Settings


def public_base_url(request: Request, settings: Settings) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")

    # Starlette applies X-Forwarded-Proto/-For when uvicorn is started with
    # --proxy-headers; Host still carries the externally visible hostname.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    # X-Forwarded-Host wins when present: it is the hostname the client used.
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        host = request.url.netloc
    # A comma-separated list can appear when several proxies are chained.
    scheme = scheme.split(",")[0].strip()
    host = host.split(",")[0].strip()
    return f"{scheme}://{host}"
