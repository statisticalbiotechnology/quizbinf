"""How build artefacts are told apart from SPA routes, and how long they cache.

The rules here are what keep a redeploy from stranding browsers on a cached
index.html that names bundles the new build no longer contains.
"""

from pathlib import Path

from app import main


def test_asset_paths_are_told_apart_from_routes():
    # Angular routes never contain a dot; every emitted artefact has one.
    for route in ("login", "teacher", "s/abc123", "teacher/session/abc123/join"):
        assert not main.looks_like_asset(route), route

    for asset in (
        "main-3WBHVWMP.js",
        "chunk-H3QWKNOY.js",
        "styles-5Z6IZAMC.css",
        "favicon.ico",
        "media/figure.png",
    ):
        assert main.looks_like_asset(asset), asset


def test_only_fingerprinted_bundles_are_cached_forever():
    # A fingerprinted name describes exactly one build, so it can never go
    # stale — the next build emits a different name.
    for name in ("main-3WBHVWMP.js", "chunk-H3QWKNOY.js", "styles-5Z6IZAMC.css"):
        assert main.cache_control_for(Path(name)) == "public, max-age=31536000, immutable", name

    # Everything else may be replaced in place by a deploy, so it must be
    # revalidated. index.html above all: it names the fingerprinted bundles.
    for name in ("index.html", "favicon.ico", "manifest.webmanifest"):
        assert main.cache_control_for(Path(name)) == "no-cache", name
