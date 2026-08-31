from __future__ import annotations

import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import jobs
from app.config import settings
from app.services import zip as zip_service


def test_create_makes_the_expected_layout():
    job_id, directory = jobs.create()
    assert jobs.JOB_ID_RE.match(job_id)
    for name in ("output", "thumbs"):
        assert (directory / name).is_dir()
    # Frames are streamed, never staged on disk.
    assert not (directory / "frames").exists()


def test_metadata_round_trips():
    job_id, _ = jobs.create()
    jobs.write_meta(job_id, {"job_id": job_id, "status": "uploaded"})
    assert jobs.read_meta(job_id)["status"] == "uploaded"


def test_non_uuid_job_ids_are_refused():
    with pytest.raises(jobs.JobError):
        jobs.directory("../../etc")


def test_missing_job_is_refused():
    with pytest.raises(jobs.JobError):
        jobs.directory("11111111-2222-3333-4444-555555555555")


@pytest.mark.parametrize(
    "filename",
    ["../job.json", "../../etc/passwd", "frames/../../job.json", "", "a/b.jpg", ".hidden"],
)
def test_resolve_file_blocks_traversal(filename):
    job_id, directory = jobs.create()
    (directory / "output" / "frame_00001.jpg").write_bytes(b"x")
    with pytest.raises(jobs.JobError):
        jobs.resolve_file(job_id, "output", filename)


def test_resolve_file_returns_real_files():
    job_id, directory = jobs.create()
    target = directory / "output" / "frame_00001.jpg"
    target.write_bytes(b"x")
    assert jobs.resolve_file(job_id, "output", "frame_00001.jpg") == target.resolve()


def test_delete_removes_the_directory():
    job_id, directory = jobs.create()
    jobs.delete(job_id)
    assert not directory.exists()


def test_purge_expired_removes_only_stale_jobs():
    stale_id, stale_dir = jobs.create()
    fresh_id, fresh_dir = jobs.create()
    old = time.time() - (settings.job_retention_minutes * 60 + 120)
    import os

    os.utime(stale_dir, (old, old))

    assert jobs.purge_expired() == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()


def test_download_clients_are_counted_by_pseudonym():
    job_id, _ = jobs.create()
    jobs.write_meta(job_id, {"job_id": job_id, "images": []})

    jobs.register_download(job_id, "203.0.113.7")
    jobs.register_download(job_id, "203.0.113.7")  # same client, still one
    stored = jobs.read_meta(job_id)["clients"]
    assert len(stored) == 1
    assert "203.0.113.7" not in str(stored)  # only the hash is kept


def test_result_is_destroyed_after_too_many_clients():
    job_id, directory = jobs.create()
    jobs.write_meta(job_id, {"job_id": job_id, "images": []})

    for index in range(settings.max_download_clients):
        jobs.register_download(job_id, f"198.51.100.{index}")
    assert directory.exists()

    with pytest.raises(jobs.JobError):
        jobs.register_download(job_id, "198.51.100.250")
    assert not directory.exists()


def test_concurrent_downloads_keep_the_job_readable():
    """Two downloads landing together must not corrupt job.json.

    They used to share one "job.json.part" temp file: their writes interleaved
    into unreadable JSON, and the second rename raised FileNotFoundError, so the
    request 500ed and every later request called the job expired. The lightbox
    makes this the normal case - it loads an image while the grid is still
    fetching - which showed up as previews going missing part-way down the grid.
    """
    job_id, directory = jobs.create()
    jobs.write_meta(job_id, {"job_id": job_id, "images": []})

    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = [
            future.exception()
            for future in [
                pool.submit(jobs.register_download, job_id, "203.0.113.9")
                for _ in range(24)
            ]
        ]
    assert errors == [None] * 24

    meta = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    # One client, counted once, and nothing left behind by the atomic write.
    assert len(meta["clients"]) == 1
    assert not list(directory.glob("*.part"))
    assert directory.exists()


def test_concurrent_downloads_still_enforce_the_client_limit():
    """The limit has to survive the race that the lock closes."""
    job_id, directory = jobs.create()
    jobs.write_meta(job_id, {"job_id": job_id, "images": []})

    clients = [f"192.0.2.{index}" for index in range(settings.max_download_clients + 4)]
    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        results = [
            future.exception()
            for future in [
                pool.submit(jobs.register_download, job_id, client)
                for client in clients
            ]
        ]

    refused = [error for error in results if isinstance(error, jobs.JobError)]
    assert refused, "a client beyond the limit should have been refused"
    assert not directory.exists()


def test_archive_contains_every_image(tmp_path):
    files = []
    for index in range(3):
        path = tmp_path / f"frame_{index:05d}.jpg"
        path.write_bytes(b"data" * 32)
        files.append(path)

    archive = zip_service.build_archive(files=files, destination=tmp_path / "all.zip")
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == [p.name for p in files]


def test_archive_without_files_is_an_error(tmp_path):
    with pytest.raises(zip_service.ZipError):
        zip_service.build_archive(files=[], destination=tmp_path / "all.zip")
