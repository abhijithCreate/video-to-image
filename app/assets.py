"""Cache-busting URLs for static files.

The browser is free to reuse a cached ``/static/js/app.js`` after a deploy, which
shows up as the new HTML being driven by the old script. Stamping the URL with a
short content hash means a changed file is always a new URL.
"""

from __future__ import annotations

import hashlib

from app.config import BASE_DIR

STATIC_DIR = BASE_DIR / "static"

# relative path -> (mtime, version); recomputed whenever the file changes, so
# editing during development is picked up without a restart.
_versions: dict[str, tuple[float, str]] = {}


def asset_url(relative: str) -> str:
    path = STATIC_DIR / relative
    try:
        mtime = path.stat().st_mtime
    except OSError:  # pragma: no cover - missing asset, fall back to a plain URL
        return f"/static/{relative}"

    cached = _versions.get(relative)
    if cached is None or cached[0] != mtime:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        cached = (mtime, digest)
        _versions[relative] = cached

    return f"/static/{relative}?v={cached[1]}"
