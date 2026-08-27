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


def test_the_env_file_is_anchored_to_the_project_not_the_cwd():
    """A cwd-relative env_file is silently ignored from another directory: the
    app runs on defaults and nothing reports that the config was skipped."""
    from app.config import BASE_DIR, Settings

    env_file = Settings.model_config["env_file"]
    assert Path(env_file).is_absolute()
    assert Path(env_file) == BASE_DIR / ".env"


def test_an_absolute_env_file_is_honoured_from_a_foreign_directory(
    tmp_path, monkeypatch
):
    from app.config import Settings

    env_file = tmp_path / "probe.env"
    env_file.write_text("MAX_OUTPUT_IMAGES=7\n")
    monkeypatch.chdir(tmp_path)

    assert Settings(_env_file=env_file).max_output_images == 7


def test_the_example_env_documents_every_setting_it_claims_to(tmp_path):
    """.env.example is what users copy, so a stale key is a silent no-op."""
    from app.config import Settings

    example = Path(".env.example").read_text()
    keys = {
        line.split("=", 1)[0].lstrip("# ").strip()
        for line in example.splitlines()
        if "=" in line and not line.strip().startswith("##")
    }
    known = {name.upper() for name in Settings.model_fields}
    unknown = {key for key in keys if key and key.upper() not in known}
    assert not unknown, f".env.example sets keys the app ignores: {sorted(unknown)}"


# ---------- blank environment variables ----------

# Every int/bool setting, i.e. the ones "" cannot be coerced into.
BLANK_HOSTILE = (
    "MAX_UPLOAD_SIZE_MB",
    "MAX_VIDEO_DURATION_SECONDS",
    "MAX_OUTPUT_IMAGES",
    "MAX_TOTAL_OUTPUT_MB",
    "JOB_RETENTION_MINUTES",
    "MAX_DOWNLOAD_CLIENTS",
    "ALLOW_URL_UPLOADS",
    "URL_FETCH_TIMEOUT_SECONDS",
    "URL_MAX_REDIRECTS",
    "ALLOW_PRIVATE_URL_HOSTS",
    "ALLOW_MEDIA_SITE_URLS",
    "MEDIA_SITE_MAX_HEIGHT",
    "MEDIA_SITE_TIMEOUT_SECONDS",
    "PROCESS_TIMEOUT_SECONDS",
    "THUMBNAIL_SIZE",
)


@pytest.mark.parametrize("name", BLANK_HOSTILE)
def test_a_single_blank_variable_does_not_break_startup(name, monkeypatch):
    """A hosting dashboard can hold a variable with no value; it arrives as ""
    and pydantic refuses to coerce it, which killed the app at import."""
    from app.config import Settings

    monkeypatch.setenv(name, "")
    Settings()  # must not raise


def test_every_blank_variable_at_once_still_yields_the_defaults(monkeypatch):
    from app.config import Settings

    for name in BLANK_HOSTILE:
        monkeypatch.setenv(name, "")

    settings = Settings()
    assert settings.max_output_images == 500
    assert settings.process_timeout_seconds == 600
    assert settings.allow_url_uploads is True
    assert settings.media_site_timeout_seconds == 120


def test_whitespace_only_counts_as_blank(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("MAX_OUTPUT_IMAGES", "   ")
    assert Settings().max_output_images == 500


def test_a_real_value_still_overrides_the_default(monkeypatch):
    """The fix must not swallow configuration that was actually provided."""
    from app.config import Settings

    monkeypatch.setenv("MAX_OUTPUT_IMAGES", "42")
    monkeypatch.setenv("ALLOW_MEDIA_SITE_URLS", "false")
    settings = Settings()
    assert settings.max_output_images == 42
    assert settings.allow_media_site_urls is False


def test_blank_binary_paths_fall_back_to_a_resolvable_default(monkeypatch):
    """.env.example says "leave empty to resolve from PATH"; an empty string is
    not resolvable, so blank has to mean the default."""
    from app.config import Settings

    monkeypatch.setenv("FFMPEG_PATH", "")
    monkeypatch.setenv("FFPROBE_PATH", "")
    settings = Settings()
    assert settings.ffmpeg_path == "ffmpeg"
    assert settings.ffprobe_path == "ffprobe"


def test_the_whole_app_survives_a_blank_vercel_style_environment(
    tmp_path, monkeypatch
):
    """Both deployment bugs at once: a foreign cwd and blank dashboard rows."""
    for name in BLANK_HOSTILE:
        monkeypatch.setenv(name, "")
    monkeypatch.chdir(tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
