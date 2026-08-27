"""Resolving a video-site page (YouTube and friends) to a downloadable stream.

These tests are deliberately offline: yt-dlp's own network behaviour is not this
project's to verify, and a suite that reaches YouTube would fail whenever a
video is taken down. Set V2I_NETWORK_TESTS=1 to also run the one live check.
"""

from __future__ import annotations

import os

import pytest

from app.config import settings
from app.services import download, media_site

needs_network = pytest.mark.skipif(
    not os.environ.get("V2I_NETWORK_TESTS"),
    reason="set V2I_NETWORK_TESTS=1 to run the live YouTube check",
)


@pytest.fixture
def extractor_enabled(monkeypatch):
    """Treat yt-dlp as installed and skip DNS checks on the test hosts."""
    monkeypatch.setattr(media_site, "available", lambda: True)
    monkeypatch.setattr(settings, "allow_media_site_urls", True)
    monkeypatch.setattr(settings, "allow_private_url_hosts", True)


# ---------- which links go to the extractor ----------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=abc123", True),
        ("https://youtube.com/watch?v=abc123", True),
        ("https://m.youtube.com/watch?v=abc123", True),
        ("https://youtu.be/abc123", True),
        ("https://www.youtube-nocookie.com/embed/abc123", True),
        ("https://vimeo.com/123456", True),
        ("https://www.tiktok.com/@someone/video/1", True),
        ("https://example.com/clip.mp4", False),
        ("https://example.com/youtube.com/clip.mp4", False),
        # A look-alike host must not be mistaken for the real one.
        ("https://notyoutube.com.evil.test/watch?v=x", False),
        ("https://youtube.com.evil.test/watch?v=x", False),
        ("not a url", False),
        ("", False),
    ],
)
def test_media_site_hosts_are_recognised(url, expected):
    assert media_site.is_media_site_url(url) is expected


# ---------- refusals that happen before anything downloads ----------


def test_videos_over_the_duration_limit_are_refused_before_download(monkeypatch):
    monkeypatch.setattr(settings, "max_video_duration_seconds", 60)
    with pytest.raises(media_site.MediaSiteError, match="635 seconds long"):
        media_site._check_playable({"duration": 635})


def test_live_streams_are_refused():
    with pytest.raises(media_site.MediaSiteError, match="live stream"):
        media_site._check_playable({"is_live": True, "duration": 10})


def test_streams_over_the_size_limit_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 50)
    with pytest.raises(media_site.MediaSiteError, match="over the 50 MB limit"):
        media_site._check_playable({"duration": 10, "filesize": 80 * 1024 * 1024})


def test_a_short_video_within_the_limits_passes():
    assert media_site._check_playable({"duration": 12, "filesize": 1024}) is None


def test_unknown_duration_is_not_treated_as_a_failure():
    """ffprobe still enforces the limit after download, so this may pass here."""
    assert media_site._check_playable({"duration": None}) is None


def test_the_feature_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "allow_media_site_urls", False)
    assert media_site.enabled() is False
    with pytest.raises(media_site.MediaSiteError, match="disabled"):
        media_site.fetch(url="https://youtu.be/abc", directory=tmp_path)


# ---------- playlists ----------


def test_a_playlist_link_uses_its_first_entry():
    info = {"_type": "playlist", "entries": [{"id": "first"}, {"id": "second"}]}
    assert media_site._one_video(info)["id"] == "first"


def test_an_empty_playlist_is_an_error():
    with pytest.raises(media_site.MediaSiteError, match="no playable video"):
        media_site._one_video({"_type": "playlist", "entries": []})


# ---------- error mapping ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ERROR: [youtube] x: This video is unavailable", "That video is unavailable."),
        ("ERROR: [youtube] x: Private video. Sign in", "That video is private."),
        ("Sign in to confirm you're not a bot", "prove it is not a bot"),
        ("Requested format is not available", "No suitable video stream"),
        ("The read operation timed out", "took too long"),
        # Nothing recognisable must not leak yt-dlp's internals.
        ("Traceback ... /usr/lib/python3/yt_dlp/extractor.py line 42", "could not be read"),
        # "age" must not match inside an ordinary word like "message".
        ("A message about a package", "could not be read"),
    ],
)
def test_extractor_errors_become_plain_messages(raw, expected):
    assert expected in media_site._message_for(Exception(raw))


@pytest.mark.parametrize(
    "raw",
    [
        "ERROR: Unsupported URL: https://example.com/page",
        "ERROR: No suitable extractor found for URL https://example.com/page",
    ],
)
def test_an_unrecognised_site_is_its_own_error_type(raw):
    assert isinstance(media_site._error_for(Exception(raw)), media_site.UnsupportedSite)


def test_a_recognised_failure_is_not_an_unsupported_site():
    error = media_site._error_for(Exception("This video is unavailable"))
    assert isinstance(error, media_site.MediaSiteError)
    assert not isinstance(error, media_site.UnsupportedSite)


# ---------- what lands on disk ----------


def test_the_downloaded_video_is_picked_and_leftovers_cleared(tmp_path):
    (tmp_path / "input.mp4").write_bytes(b"0" * 2048)
    (tmp_path / "input.mp4.part").write_bytes(b"0" * 64)
    (tmp_path / "input.webm").write_bytes(b"0" * 16)

    chosen = media_site._saved_file(tmp_path)

    assert chosen.name == "input.mp4"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["input.mp4"]


def test_no_usable_download_is_an_error_and_leaves_nothing_behind(tmp_path):
    (tmp_path / "input.mp4.part").write_bytes(b"0" * 64)
    with pytest.raises(media_site.MediaSiteError):
        media_site._saved_file(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Big Buck Bunny", "Big Buck Bunny.mp4"),
        ("  spaced \n out  ", "spaced out.mp4"),
        ("", "video.mp4"),
        (None, "video.mp4"),
    ],
)
def test_the_display_name_comes_from_the_video_title(tmp_path, title, expected):
    path = tmp_path / "input.mp4"
    assert media_site._display_name({"title": title}, path) == expected


def test_an_overlong_title_is_truncated(tmp_path):
    name = media_site._display_name({"title": "z" * 400}, tmp_path / "input.mp4")
    assert name == "z" * 120 + ".mp4"


# ---------- extractor options ----------


def test_the_generic_extractor_is_excluded(tmp_path):
    """It accepts any URL, which would make every link look supported."""
    assert media_site._options(tmp_path)["allowed_extractors"] == ["default", "-generic"]


def test_options_cap_the_download_and_write_only_the_video(tmp_path):
    options = media_site._options(tmp_path)
    assert options["max_filesize"] == settings.max_upload_size_bytes
    assert options["outtmpl"] == str(tmp_path / "input.%(ext)s")
    assert options["noplaylist"] is True
    assert options["writethumbnail"] is False


def test_the_format_selector_caps_the_height(monkeypatch):
    monkeypatch.setattr(settings, "media_site_max_height", 720)
    selector = media_site._format_selector()
    assert "[height<=?720]" in selector
    # Video-only streams come first: the audio track would only be discarded.
    assert selector.startswith("bv*")


# ---------- routing through download.fetch ----------


def test_a_youtube_link_goes_straight_to_the_extractor(
    monkeypatch, tmp_path, extractor_enabled
):
    seen = []

    def fake_fetch(*, url, directory):
        seen.append(url)
        target = directory / "input.mp4"
        target.write_bytes(b"0" * 32)
        return target, "Clip.mp4"

    monkeypatch.setattr(media_site, "fetch", fake_fetch)

    path, display = download.fetch(
        url="https://www.youtube.com/watch?v=abc123", directory=tmp_path
    )

    assert seen == ["https://www.youtube.com/watch?v=abc123"]
    assert path.name == "input.mp4"
    assert display == "Clip.mp4"


def test_extractor_failures_surface_as_download_errors(
    monkeypatch, tmp_path, extractor_enabled
):
    def fake_fetch(*, url, directory):
        raise media_site.MediaSiteError("That video is private.")

    monkeypatch.setattr(media_site, "fetch", fake_fetch)

    with pytest.raises(download.DownloadError, match="That video is private."):
        download.fetch(url="https://youtu.be/abc123", directory=tmp_path)


def test_a_non_video_link_falls_back_to_the_extractor(
    monkeypatch, tmp_path, extractor_enabled
):
    def refuse_direct(url, directory):
        raise download.NotADirectVideo("That link does not look like a video file.")

    def fake_fetch(*, url, directory):
        target = directory / "input.mp4"
        target.write_bytes(b"0" * 32)
        return target, "Recovered.mp4"

    monkeypatch.setattr(download, "_fetch_direct", refuse_direct)
    monkeypatch.setattr(media_site, "fetch", fake_fetch)

    _, display = download.fetch(url="https://example.test/page", directory=tmp_path)
    assert display == "Recovered.mp4"


def test_an_unrecognised_page_keeps_the_clearer_direct_message(
    monkeypatch, tmp_path, extractor_enabled
):
    """No extractor claimed it either, so "not a video file" is more useful."""

    def refuse_direct(url, directory):
        raise download.NotADirectVideo("That link does not look like a video file.")

    def fake_fetch(*, url, directory):
        raise media_site.UnsupportedSite("That link is not from a site ...")

    monkeypatch.setattr(download, "_fetch_direct", refuse_direct)
    monkeypatch.setattr(media_site, "fetch", fake_fetch)

    with pytest.raises(download.DownloadError, match="does not look like a video file"):
        download.fetch(url="https://example.test/page", directory=tmp_path)


def test_with_the_extractor_off_a_non_video_link_is_simply_refused(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "allow_media_site_urls", False)

    def refuse_direct(url, directory):
        raise download.NotADirectVideo("That link does not look like a video file.")

    monkeypatch.setattr(download, "_fetch_direct", refuse_direct)

    with pytest.raises(download.DownloadError, match="does not look like a video file"):
        download.fetch(url="https://example.test/page", directory=tmp_path)


# ---------- live check, opt-in ----------


@needs_network
def test_a_real_youtube_link_is_resolved(tmp_path):
    path, display = download.fetch(
        url="https://www.youtube.com/watch?v=KSoD_GKhg38", directory=tmp_path
    )
    assert path.name == "input.mp4"
    assert path.stat().st_size > 0
    assert display.endswith(".mp4")


# ---------- bot checks and the client fallback ----------


@pytest.mark.parametrize(
    "raw",
    [
        "ERROR: [youtube] x: Sign in to confirm you're not a bot",
        "ERROR: [youtube] x: The page needs to be reloaded",
        "Failed to extract any player response",
        "Please sign in",
    ],
)
def test_a_bot_check_is_its_own_retryable_error(raw):
    assert isinstance(media_site._error_for(Exception(raw)), media_site.Challenged)


@pytest.mark.parametrize(
    "raw", ["Private video", "This video is unavailable", "No suitable extractor"]
)
def test_final_failures_are_not_marked_retryable(raw):
    assert not isinstance(media_site._error_for(Exception(raw)), media_site.Challenged)


def test_a_bot_check_is_retried_with_a_fallback_client(monkeypatch, tmp_path):
    """The default client is challenged; a different endpoint still works."""
    tried: list[str | None] = []

    def fake_once(url, directory, client):
        tried.append(client)
        if client is None:
            raise media_site.Challenged("prove it is not a bot")
        target = directory / "input.mp4"
        target.write_bytes(b"0" * 32)
        return target, "Clip.mp4"

    monkeypatch.setattr(media_site, "_fetch_once", fake_once)

    path, display = media_site.fetch(url="https://youtu.be/abc", directory=tmp_path)

    assert tried == [None, *media_site.CLIENT_FALLBACKS]
    assert (path.name, display) == ("input.mp4", "Clip.mp4")


def test_a_final_failure_is_not_retried(monkeypatch, tmp_path):
    """Retrying a private video only provokes the site for no gain."""
    tried: list[str | None] = []

    def fake_once(url, directory, client):
        tried.append(client)
        raise media_site.MediaSiteError("That video is private.")

    monkeypatch.setattr(media_site, "_fetch_once", fake_once)

    with pytest.raises(media_site.MediaSiteError, match="private"):
        media_site.fetch(url="https://youtu.be/abc", directory=tmp_path)
    assert tried == [None]


def test_the_bot_check_surfaces_once_every_client_is_exhausted(monkeypatch, tmp_path):
    def fake_once(url, directory, client):
        (directory / "input.mp4.part").write_bytes(b"junk")
        raise media_site.Challenged("prove it is not a bot")

    monkeypatch.setattr(media_site, "_fetch_once", fake_once)

    with pytest.raises(media_site.Challenged, match="not a bot"):
        media_site.fetch(url="https://youtu.be/abc", directory=tmp_path)
    # Nothing half-downloaded is left for the job to trip over.
    assert list(tmp_path.iterdir()) == []


def test_only_clients_that_can_finish_a_download_are_used():
    """android_vr extracts fine and then 403s on the media URLs."""
    assert "android_vr" not in media_site.CLIENT_FALLBACKS
    assert media_site.CLIENT_FALLBACKS


def test_a_player_client_is_only_pinned_when_one_is_asked_for(tmp_path):
    assert "extractor_args" not in media_site._options(tmp_path)
    pinned = media_site._options(tmp_path, client="android")
    assert pinned["extractor_args"] == {"youtube": {"player_client": ["android"]}}


def test_leftovers_are_cleared_between_attempts(tmp_path):
    (tmp_path / "input.mp4").write_bytes(b"x")
    (tmp_path / "input.mp4.part").write_bytes(b"x")
    (tmp_path / "keep.txt").write_bytes(b"x")

    media_site._clear(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


# ---------- optional cookies ----------


def test_no_cookies_are_sent_by_default():
    assert media_site._cookie_options() == {}


def test_a_cookie_file_is_used_when_it_exists(monkeypatch, tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(settings, "youtube_cookies_file", jar)
    assert media_site._cookie_options() == {"cookiefile": str(jar)}


def test_a_missing_cookie_file_is_ignored(monkeypatch, tmp_path):
    """A bad path must not break every fetch with an unrelated error."""
    monkeypatch.setattr(settings, "youtube_cookies_file", tmp_path / "nope.txt")
    assert media_site._cookie_options() == {}


def test_cookies_can_come_from_a_browser(monkeypatch):
    monkeypatch.setattr(settings, "youtube_cookies_from_browser", "firefox")
    assert media_site._cookie_options() == {"cookiesfrombrowser": ("firefox",)}


def test_the_retry_chain_stops_when_the_budget_is_spent(monkeypatch, tmp_path):
    """A challenged attempt can burn minutes on its own retries."""
    monkeypatch.setattr(settings, "media_site_timeout_seconds", 0)
    tried: list[str | None] = []

    def fake_once(url, directory, client):
        tried.append(client)
        raise media_site.Challenged("prove it is not a bot")

    monkeypatch.setattr(media_site, "_fetch_once", fake_once)

    with pytest.raises(media_site.Challenged):
        media_site.fetch(url="https://youtu.be/abc", directory=tmp_path)
    # The first attempt still happens; the fallback is skipped.
    assert tried == [None]
