from __future__ import annotations

import time
import zipfile

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
