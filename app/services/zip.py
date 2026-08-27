"""ZIP packaging for the "download all" action."""

from __future__ import annotations

import zipfile
from pathlib import Path


class ZipError(Exception):
    """A user-facing problem with archive creation."""


def build_archive(*, files: list[Path], destination: Path) -> Path:
    """Write (or reuse) a ZIP archive containing ``files``.

    Images are already compressed, so entries are stored rather than deflated:
    it is markedly faster and the size difference is negligible.
    """
    if not files:
        raise ZipError("There are no images to download.")

    newest = max(path.stat().st_mtime for path in files)
    if destination.exists() and destination.stat().st_mtime >= newest:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
    partial.replace(destination)
    return destination
