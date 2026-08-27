"""The app must import and serve from any working directory.

A serverless host imports the module with a working directory of its own
(Vercel uses /var/task). A cwd-relative ``StaticFiles(directory="static")``
raised at import time, so ``app = create_app()`` never completed and the whole
function died with FUNCTION_INVOCATION_FAILED - before any exception handler
existed to report it.

The rest of the suite cannot catch this: tests/conftest.py chdirs to the project
root, which is exactly the condition that hides the bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_bundled_paths_do_not_depend_on_the_working_directory():
    from app.assets import STATIC_DIR
    from app.config import BASE_DIR
    from app.routes import templates

    assert BASE_DIR.is_absolute()
    assert STATIC_DIR.is_absolute() and STATIC_DIR.is_dir()
    for entry in templates.env.loader.searchpath:
        assert Path(entry).is_absolute(), entry
        assert Path(entry).is_dir(), entry


def test_the_app_can_be_built_and_served_from_a_foreign_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    from app.main import create_app

    app = create_app()  # StaticFiles used to raise right here

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Video to Image" in page.text  # the template resolved too
        assert client.get("/health").status_code == 200
        assert client.get("/static/js/app.js").status_code == 200


def test_asset_versioning_still_works_from_a_foreign_directory(tmp_path, monkeypatch):
    """asset_url falls back to an unversioned URL when it cannot stat a file."""
    monkeypatch.chdir(tmp_path)

    from app.assets import asset_url

    assert "?v=" in asset_url("js/app.js")


def test_missing_ffmpeg_is_reported_as_a_clean_error_not_a_crash(
    client, monkeypatch, tmp_path
):
    """Vercel's Python runtime ships no FFmpeg, so this is its normal state."""
    from app.config import settings

    monkeypatch.setattr(settings, "ffmpeg_path", str(tmp_path / "no-ffmpeg"))
    monkeypatch.setattr(settings, "ffprobe_path", str(tmp_path / "no-ffprobe"))

    assert client.get("/health").json()["ffmpeg"] is False

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 2048)
    with video.open("rb") as handle:
        response = client.post(
            "/api/upload", files={"file": ("clip.mp4", handle, "video/mp4")}
        )

    assert response.status_code == 400
    assert "FFmpeg not found" in response.json()["error"]


@pytest.mark.parametrize("path", ["/", "/health"])
def test_the_page_still_serves_without_ffmpeg(client, monkeypatch, tmp_path, path):
    """The UI must load even where video processing cannot run."""
    from app.config import settings

    monkeypatch.setattr(settings, "ffmpeg_path", str(tmp_path / "no-ffmpeg"))
    assert client.get(path).status_code == 200
