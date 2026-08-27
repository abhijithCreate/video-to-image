"""Fetching a video from a pasted URL, and refusing to be an SSRF proxy."""

from __future__ import annotations

import http.server
import subprocess
import threading
import pytest

from app.config import settings
from app.services import download, video as video_service

needs_ffmpeg = pytest.mark.skipif(
    not video_service.ffmpeg_available(), reason="FFmpeg is not installed"
)


@pytest.fixture
def media_server(tmp_path):
    """A local HTTP server that serves one real video, plus a few edge cases."""
    video = tmp_path / "clip.mp4"
    if video_service.ffmpeg_available():
        subprocess.run(
            [
                settings.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                "-pix_fmt", "yuv420p", str(video),
            ],
            check=True,
            timeout=60,
        )
        payload = video.read_bytes()
    else:
        # Tests that need a decodable video are marked needs_ffmpeg; the rest only
        # care about how the transfer itself behaves.
        payload = b"0" * 4096

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output clean
            pass

        def do_GET(self):  # noqa: N802
            if self.path == "/clip.mp4":
                body = payload
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
            elif self.path == "/untyped":
                body = payload
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
            elif self.path == "/notes.txt":
                body = b"this is not a video"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            elif self.path == "/huge.mp4":
                body = b"0" * (3 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/clip.mp4")
                self.end_headers()
                return
            elif self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.end_headers()
                return
            elif self.path == "/missing":
                self.send_error(404)
                return
            else:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class QuietServer(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # a client hanging up mid-transfer is expected in these tests

    server = QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", len(payload)
    server.shutdown()
    server.server_close()


@pytest.fixture
def allow_local(monkeypatch):
    """The server is 127.0.0.1, which is blocked unless explicitly allowed."""
    monkeypatch.setattr(settings, "allow_private_url_hosts", True)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/clip.mp4",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not a url",
        "",
    ],
)
def test_only_http_urls_are_accepted(url, tmp_path):
    with pytest.raises(download.DownloadError):
        download.fetch(url=url, directory=tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/clip.mp4",
        "http://localhost/clip.mp4",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata service
        "http://10.0.0.5/clip.mp4",
        "http://192.168.1.10/clip.mp4",
        "http://[::1]/clip.mp4",
    ],
)
def test_private_and_metadata_addresses_are_refused(url, tmp_path):
    """Without this the server would happily probe its own network."""
    with pytest.raises(download.DownloadError, match="private address|could not be found"):
        download.fetch(url=url, directory=tmp_path)


def test_overlong_urls_are_refused(tmp_path):
    with pytest.raises(download.DownloadError):
        download.fetch(url="https://example.com/" + "a" * 3000, directory=tmp_path)


@needs_ffmpeg
def test_fetches_a_real_video(media_server, allow_local, tmp_path):
    base, size = media_server
    path, name = download.fetch(url=f"{base}/clip.mp4", directory=tmp_path)
    assert path.name == "input.mp4"
    assert path.stat().st_size == size
    assert name == "clip.mp4"


@needs_ffmpeg
def test_follows_a_redirect(media_server, allow_local, tmp_path):
    base, size = media_server
    path, _ = download.fetch(url=f"{base}/redirect", directory=tmp_path)
    assert path.stat().st_size == size


def test_redirect_loops_are_bounded(media_server, allow_local, tmp_path):
    base, _ = media_server
    with pytest.raises(download.DownloadError, match="redirects too many times"):
        download.fetch(url=f"{base}/loop", directory=tmp_path)


def test_non_video_links_are_refused(media_server, allow_local, tmp_path):
    base, _ = media_server
    with pytest.raises(download.DownloadError, match="does not look like a video"):
        download.fetch(url=f"{base}/notes.txt", directory=tmp_path)


def test_oversized_downloads_are_stopped(media_server, allow_local, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    base, _ = media_server
    with pytest.raises(download.DownloadError, match="larger than"):
        download.fetch(url=f"{base}/huge.mp4", directory=tmp_path)


def test_http_errors_are_reported_plainly(media_server, allow_local, tmp_path):
    base, _ = media_server
    with pytest.raises(download.DownloadError, match=r"error \(404\)"):
        download.fetch(url=f"{base}/missing", directory=tmp_path)


def test_url_uploads_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_url_uploads", False)
    with pytest.raises(download.DownloadError, match="disabled"):
        download.fetch(url="https://example.com/clip.mp4", directory=tmp_path)


@needs_ffmpeg
def test_url_endpoint_creates_a_usable_job(client, media_server, allow_local):
    base, _ = media_server
    response = client.post("/api/upload-url", json={"url": f"{base}/clip.mp4"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["video"]["format_name"] == "MP4"      # not "MOV"
    assert body["video"]["width"] == 160

    job_id = body["job_id"]
    preview = client.get(f"/api/preview/{job_id}")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"

    result = client.post(
        "/api/process", json={"job_id": job_id, "method": "count", "count": 3}
    )
    assert result.status_code == 200
    assert result.json()["count"] == 3


def test_url_endpoint_rejects_private_targets(client):
    response = client.post("/api/upload-url", json={"url": "http://169.254.169.254/"})
    assert response.status_code == 400
    assert "private address" in response.json()["error"]


def test_url_endpoint_leaves_no_job_behind_on_failure(client):
    client.post("/api/upload-url", json={"url": "http://10.0.0.1/clip.mp4"})
    assert list(settings.temp_dir.glob("*")) == []


def test_preview_of_unknown_job_is_not_found(client):
    assert client.get("/api/preview/not-a-uuid").status_code == 404
