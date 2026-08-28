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

import re
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


def test_a_missing_decoder_is_reported_as_a_clean_error_not_a_crash(
    client, monkeypatch, tmp_path
):
    """The decoder ships in a wheel now, so this only happens on a broken
    install - but it must still be a 400, not a stack trace."""
    from app.services import video as video_service

    def no_decoder():
        raise video_service.VideoError(
            "Video processing is unavailable on this server (no video decoder)."
        )

    monkeypatch.setattr(video_service, "_av", no_decoder)
    monkeypatch.setattr(video_service, "ffmpeg_available", lambda: False)

    assert client.get("/health").json()["ffmpeg"] is False

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 2048)
    with video.open("rb") as handle:
        response = client.post(
            "/api/upload", files={"file": ("clip.mp4", handle, "video/mp4")}
        )

    assert response.status_code == 400
    assert "unavailable on this server" in response.json()["error"]


@pytest.mark.parametrize("path", ["/", "/health"])
def test_the_page_still_serves_without_a_decoder(client, monkeypatch, path):
    """The UI must load even where video processing cannot run."""
    from app.services import video as video_service

    monkeypatch.setattr(video_service, "ffmpeg_available", lambda: False)
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


# ---------- container-host deployment ----------


def test_the_container_listens_on_an_injected_port():
    """Render and Railway assign $PORT at run time, so it cannot be baked in;
    a container that ignores it fails their health checks."""
    dockerfile = Path("Dockerfile").read_text()
    assert "${PORT:-8000}" in dockerfile
    # `exec` keeps uvicorn as PID 1 so the platform's SIGTERM reaches it.
    assert 'CMD ["sh", "-c", "exec uvicorn' in dockerfile
    # The healthcheck has to follow the same port.
    assert "os.environ.get('PORT'" in dockerfile


def _config_env_keys() -> dict[str, set[str]]:
    """Env keys declared in the deploy configs, without a YAML dependency."""
    import tomllib

    fly = set(tomllib.loads(Path("fly.toml").read_text())["env"])
    render = set(
        re.findall(r"^\s*-\s*key:\s*([A-Z0-9_]+)\s*$", Path("render.yaml").read_text(), re.M)
    )
    return {"fly.toml": fly, "render.yaml": render}


def test_deploy_configs_only_set_settings_that_exist():
    """A typo'd key in a deploy config is a silent no-op."""
    from app.config import Settings

    known = {name.upper() for name in Settings.model_fields}
    for label, keys in _config_env_keys().items():
        assert keys, f"{label}: parsed no env keys"
        assert not (keys - known), f"{label} sets unknown keys: {sorted(keys - known)}"


def test_deploy_configs_pin_a_single_instance():
    """Jobs live on one instance's /tmp: the later process and download
    requests must reach the same instance, so scaling out breaks conversions."""
    import tomllib

    render = Path("render.yaml").read_text()
    assert re.search(r"^\s*numInstances:\s*1\s*$", render, re.M)

    fly = tomllib.loads(Path("fly.toml").read_text())
    # One machine kept warm, so an in-flight conversion is never interrupted.
    assert fly["http_service"]["min_machines_running"] == 1
    assert fly["http_service"]["internal_port"] == 8000


def test_deploy_configs_run_in_production_mode():
    from app.config import Settings

    for label, keys in _config_env_keys().items():
        assert "APP_ENV" in keys, label
    assert Settings(_env_file=None, app_env="production").is_production is True


def test_deploy_configs_enable_video_site_links():
    """Enabled on request. It needs YOUTUBE_COOKIES to be reliable from a
    datacenter IP, and the configs say so in a comment."""
    import json
    import tomllib

    assert tomllib.loads(Path("fly.toml").read_text())["env"][
        "ALLOW_MEDIA_SITE_URLS"
    ] == "true"
    assert "YOUTUBE_COOKIES" in Path("fly.toml").read_text()

    render = Path("render.yaml").read_text()
    block = render[render.index("ALLOW_MEDIA_SITE_URLS") :]
    assert re.search(r'value:\s*"true"', block.split("- key:")[0])
    assert "YOUTUBE_COOKIES" in render

    vercel = json.loads(Path("vercel.json").read_text())
    assert vercel["env"]["ALLOW_MEDIA_SITE_URLS"] == "true"


def test_the_page_offers_no_upload_where_processing_cannot_work(
    client, monkeypatch
):
    """Inviting an upload that is guaranteed to fail is worse than saying so."""
    from app.services import video as video_service

    monkeypatch.setattr(video_service, "ffmpeg_available", lambda: False)

    body = client.get("/").text
    assert "Video processing is unavailable on this server" in body
    assert "docker compose up --build" in body
    # The controls are inert rather than merely styled as such.
    assert "disabled" in body.split('id="file-input"')[1][:250]
    # The URL field needs ffprobe to verify what arrives, so it goes too.
    assert 'id="url-form"' not in body


def test_the_upload_form_is_untouched_where_ffmpeg_is_present(client, monkeypatch):
    """Simulated rather than assumed: this suite also runs where FFmpeg is
    absent, which is exactly the state the previous test covers."""
    from app.services import video as video_service

    monkeypatch.setattr(video_service, "ffmpeg_available", lambda: True)

    body = client.get("/").text
    assert "Video processing is unavailable on this server" not in body
    assert "disabled" not in body.split('id="file-input"')[1][:250]
    assert 'id="url-form"' in body


# ---------- decoding without a binary ----------


def test_the_app_shells_out_to_no_video_binary():
    """Decoding runs in-process. A subprocess call would reintroduce the exact
    dependency that made serverless hosting impossible."""
    source = Path("app/services/video.py").read_text()
    # Checked against the parsed module, not the prose: the docstring explains
    # why the subprocess went away and would match a naive grep.
    import ast

    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "subprocess" not in imported
    assert "shutil" not in imported
    # PyAV is imported lazily inside helpers, so look for the call sites.
    assert "import av" in source


def test_the_production_dependency_set_stays_lean():
    """A serverless bundle is capped at 250 MB unzipped. av (97 MB) and yt-dlp
    (24 MB) earn their place; these do not."""
    prod = Path("requirements.txt").read_text()
    assert "av==" in prod
    assert "yt-dlp==" in prod
    for excluded in ("pytest", "httpx", "numpy", "uvicorn[standard]"):
        assert excluded not in prod, f"{excluded} belongs in requirements-dev.txt"

    dev = Path("requirements-dev.txt").read_text()
    assert "-r requirements.txt" in dev
    for expected in ("pytest", "httpx", "uvicorn[standard]"):
        assert expected in dev


def test_the_bundle_cap_allows_the_decoder():
    """35mb predated bundling a decoder and would fail the build outright."""
    import json

    config = json.loads(Path("vercel.json").read_text())
    assert config["builds"][0]["config"]["maxLambdaSize"] == "250mb"


def test_vercel_converts_statelessly_and_container_hosts_do_not():
    """Vercel spreads requests across instances, so a job cannot be kept. A
    single-instance container host can, and keeps the preview grid."""
    import json
    import tomllib

    vercel = json.loads(Path("vercel.json").read_text())
    assert vercel["env"]["STATELESS_CONVERSION"] == "true"

    assert "STATELESS_CONVERSION" not in tomllib.loads(Path("fly.toml").read_text())["env"]
    assert "STATELESS_CONVERSION" not in Path("render.yaml").read_text()
