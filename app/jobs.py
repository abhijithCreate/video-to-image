"""Job lifecycle: temporary directories, metadata and cleanup.

A job is a directory under ``TEMP_DIR`` named with a UUID4:

    <TEMP_DIR>/<job-id>/
        input.mp4
        job.json
        output/
        thumbs/
        images.zip

Frames are piped from FFmpeg straight into Pillow, so there is no intermediate
frame directory. Metadata lives on disk next to the files so any worker can serve
a job, and so nothing survives the retention sweep.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from app.config import settings

JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

META_FILENAME = "job.json"
ARCHIVE_FILENAME = "images.zip"


class JobError(Exception):
    """A user-facing problem locating or reading a job."""


def root() -> Path:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings.temp_dir


def create() -> tuple[str, Path]:
    job_id = str(uuid.uuid4())
    directory = root() / job_id
    (directory / "output").mkdir(parents=True, exist_ok=True)
    (directory / "thumbs").mkdir(parents=True, exist_ok=True)
    return job_id, directory


def directory(job_id: str) -> Path:
    """Return an existing job directory, rejecting anything that is not a UUID."""
    if not JOB_ID_RE.match(job_id or ""):
        raise JobError("Unknown job.")
    path = root() / job_id
    if not path.is_dir():
        raise JobError("This job has expired or no longer exists.")
    return path


def write_meta(job_id: str, meta: dict) -> None:
    path = directory(job_id) / META_FILENAME
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_meta(job_id: str) -> dict:
    path = directory(job_id) / META_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobError("This job has expired or no longer exists.") from exc


def resolve_file(job_id: str, subdir: str, filename: str) -> Path:
    """Resolve a generated file, refusing traversal and unexpected names."""
    if not SAFE_NAME_RE.match(filename or ""):
        raise JobError("Unknown file.")

    base = (directory(job_id) / subdir).resolve()
    candidate = (base / filename).resolve()
    if candidate.parent != base or not candidate.is_file():
        raise JobError("Unknown file.")
    return candidate


def input_file(job_id: str) -> Path:
    """The uploaded video for a job.

    Deliberately narrower than :func:`resolve_file`: only the recorded input
    name is servable, so nothing else in the job directory (``job.json``
    included) can be reached through the preview endpoint.
    """
    name = read_meta(job_id).get("input") or ""
    if not SAFE_NAME_RE.match(name) or not name.startswith("input."):
        raise JobError("Unknown file.")
    path = directory(job_id) / name
    if not path.is_file():
        raise JobError("This job has expired or no longer exists.")
    return path


def output_files(job_id: str) -> list[Path]:
    meta = read_meta(job_id)
    base = directory(job_id) / "output"
    return [
        base / image["filename"]
        for image in meta.get("images", [])
        if (base / image["filename"]).is_file()
    ]


def _client_token(job_id: str, client: str | None) -> str:
    """A per-job pseudonym for a client address.

    Only the hash is stored, so counting distinct downloaders never means keeping
    a record of who they were.
    """
    return hashlib.sha256(f"{job_id}:{client or 'unknown'}".encode()).hexdigest()[:32]


def register_download(job_id: str, client: str | None) -> None:
    """Record a downloader, destroying the result once too many have seen it.

    This is what makes the "removed sooner if accessed from several addresses"
    promise real: a result URL that gets shared around stops working.
    """
    meta = read_meta(job_id)
    token = _client_token(job_id, client)
    seen = meta.get("clients") or []
    if token in seen:
        return

    if len(seen) >= settings.max_download_clients:
        delete(job_id)
        raise JobError(
            "This result was removed because it was downloaded from too many "
            "different addresses. Please convert the video again."
        )

    meta["clients"] = seen + [token]
    write_meta(job_id, meta)


def delete(job_id: str) -> None:
    shutil.rmtree(directory(job_id), ignore_errors=True)


def purge_expired() -> int:
    """Remove job directories older than the retention window."""
    cutoff = time.time() - settings.job_retention_minutes * 60
    removed = 0
    try:
        entries = list(root().iterdir())
    except OSError:  # pragma: no cover - temp dir unreadable
        return 0

    for entry in entries:
        if not entry.is_dir() or not JOB_ID_RE.match(entry.name):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:  # pragma: no cover - raced with another sweep
            continue
    return removed


def touch(job_id: str) -> None:
    """Refresh a job's mtime so active work is not swept away."""
    try:
        (root() / job_id).touch()
    except OSError:  # pragma: no cover
        pass
