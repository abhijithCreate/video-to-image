"""Resolving a video from a media-site page (YouTube and similar) with yt-dlp.

The direct path in :mod:`app.services.download` can only handle a URL that *is*
the video file. A YouTube watch URL serves an HTML player page with the media
behind separate adaptive streams, so it needs an extractor to resolve.

Only frames are ever wanted downstream, so a video-only stream is preferred:
there is no point muxing an audio track that is about to be discarded, and
skipping the merge keeps the download to a single stream.

yt-dlp is imported lazily so a deployment without it still starts and serves
direct links; the feature reports itself as unavailable instead.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from app.config import ALLOWED_VIDEO_EXTENSIONS, settings


class MediaSiteError(Exception):
    """A user-facing problem resolving a media-site link."""


class UnsupportedSite(MediaSiteError):
    """No extractor recognised the link at all.

    Kept distinct so a caller that only tried the extractor as a fallback can
    report its own, plainer failure instead of this one.
    """


class Challenged(MediaSiteError):
    """The site demanded proof the client is not a bot.

    Kept distinct because it is the one failure worth retrying: a different
    player client talks to a different endpoint, which is often not challenged.
    """


# Hosts that are always a page and never a direct file, so they can go straight
# to the extractor without a wasted request first. Anything else still reaches
# the extractor through download.fetch's fallback, so this list is a shortcut,
# not the set of supported sites.
MEDIA_SITE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "vimeo.com",
        "dailymotion.com",
        "dai.ly",
        "twitch.tv",
        "streamable.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "reddit.com",
    }
)

# yt-dlp failures are verbose and full of internals. These map the common ones
# onto something a user can act on; anything unrecognised gets a generic line.
_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    (
        "sign in to confirm",
        "That site is asking this server to prove it is not a bot. This is "
        "usually temporary - wait a minute and try again, or download the video "
        "yourself and upload the file.",
    ),
    (
        "page needs to be reloaded",
        "That site would not serve the video to this server. Try again in a "
        "minute, or download the video yourself and upload the file.",
    ),
    ("private video", "That video is private."),
    ("members-only", "That video is members-only."),
    ("video is unavailable", "That video is unavailable."),
    ("video unavailable", "That video is unavailable."),
    ("removed by the uploader", "That video has been removed."),
    ("age-restricted", "That video is age-restricted, so it cannot be fetched."),
    ("confirm your age", "That video is age-restricted, so it cannot be fetched."),
    ("not available in your country", "That video is blocked in this server's region."),
    ("geo restriction", "That video is blocked in this server's region."),
    ("no video formats", "No downloadable video was found at that link."),
    (
        "requested format is not available",
        "No suitable video stream was found at that link.",
    ),
    ("timed out", "That link took too long to respond."),
)

_GENERIC_ERROR = "That link could not be read as a video."

# YouTube challenges the default (web) client by IP, especially from datacenters
# and after a burst of requests. The android client talks to a different endpoint
# and still serves a complete progressive stream when the default is blocked, so
# it is worth one retry. Quality is lower - a working 360p beats nothing.
#
# Only clients verified to finish a *download* belong here: android_vr extracts
# happily and then 403s on the media URLs, which is worse than not trying.
CLIENT_FALLBACKS: tuple[str, ...] = ("android",)

# Wording YouTube uses when it wants a signed-in or attested client.
_CHALLENGE_SIGNS: tuple[str, ...] = (
    "sign in to confirm",
    "not a bot",
    "please sign in",
    "page needs to be reloaded",
    "failed to extract any player response",
)
_UNSUPPORTED_MESSAGE = (
    "That link is not from a site this server can read. Paste a direct link to "
    "a video file instead."
)


class _SilentLogger:
    """Discards yt-dlp's own console output; errors surface as exceptions."""

    def debug(self, message: str) -> None:
        pass

    info = debug
    warning = debug
    error = debug


def available() -> bool:
    """Whether yt-dlp is installed, so the UI and /health can say so."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def enabled() -> bool:
    return bool(settings.allow_media_site_urls) and available()


def is_media_site_url(url: str) -> bool:
    """Whether the URL's host is a known page-only media site."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower().strip(".")
    if not host:
        return False
    labels = host.split(".")
    # www.youtube.com and m.youtube.com both match "youtube.com".
    return any(
        ".".join(labels[index:]) in MEDIA_SITE_HOSTS for index in range(len(labels))
    )


def _yt_dlp():
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise MediaSiteError(
            "This server cannot read links from video sites. Paste a direct link "
            "to a video file instead."
        ) from exc
    return yt_dlp


def _message_for(error: BaseException) -> str:
    text = str(error).lower()
    for needle, message in _ERROR_HINTS:
        if needle in text:
            return message
    return _GENERIC_ERROR


# What yt-dlp says when no extractor claims the link. The wording differs
# depending on whether the generic extractor was excluded by name.
_NO_EXTRACTOR = ("unsupported url", "no suitable extractor")


def _error_for(error: BaseException) -> MediaSiteError:
    """Map a yt-dlp failure onto the right user-facing exception."""
    text = str(error).lower()
    if any(needle in text for needle in _NO_EXTRACTOR):
        return UnsupportedSite(_UNSUPPORTED_MESSAGE)
    if any(needle in text for needle in _CHALLENGE_SIGNS):
        return Challenged(_message_for(error))
    return MediaSiteError(_message_for(error))


def _format_selector() -> str:
    """Prefer a video-only stream, smallest decode cost first."""
    cap = f"[height<=?{settings.media_site_max_height}]"
    return "/".join(
        (
            f"bv*{cap}[vcodec^=avc1]",  # H.264: fastest and most portable
            f"bv*{cap}[ext=mp4]",
            f"bv*{cap}",
            f"b{cap}[ext=mp4]",  # progressive, when there is no adaptive stream
            f"b{cap}",
            "b",
        )
    )


def _cookie_options() -> dict[str, Any]:
    """Credentials for a site that will not serve an anonymous client."""
    options: dict[str, Any] = {}
    cookie_file = settings.youtube_cookies_file
    if cookie_file and Path(cookie_file).is_file():
        options["cookiefile"] = str(cookie_file)
    browser = (settings.youtube_cookies_from_browser or "").strip()
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    return options


def _options(directory: Path, *, client: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        # A link to a playlist should yield one video, not the whole list.
        "noplaylist": True,
        # Without this, yt-dlp's "generic" extractor accepts *any* URL and tries
        # to scrape media out of it. That makes every link look supported, and
        # re-fetches arbitrary URLs this module has no reason to touch. Only real
        # site extractors should engage here; a plain file URL is the direct
        # path's job.
        "allowed_extractors": ["default", "-generic"],
        # Nothing but the video itself may be written into the job directory.
        "writethumbnail": False,
        "writesubtitles": False,
        "writeinfojson": False,
        "outtmpl": str(directory / "input.%(ext)s"),
        "format": _format_selector(),
        "max_filesize": settings.max_upload_size_bytes,
        "socket_timeout": min(settings.url_fetch_timeout_seconds, 30),
        "retries": 2,
        "fragment_retries": 2,
        "overwrites": True,
        "logger": _SilentLogger(),
    }
    if client:
        options["extractor_args"] = {"youtube": {"player_client": [client]}}
    options.update(_cookie_options())
    return options


def _extract(
    url: str, *, directory: Path, download: bool, client: str | None = None
) -> dict[str, Any]:
    yt_dlp = _yt_dlp()
    options = _options(directory, client=client)
    if not download:
        options["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=download)
    except MediaSiteError:
        raise
    except Exception as exc:  # yt-dlp raises a wide range of its own errors
        raise _error_for(exc) from exc

    if not isinstance(info, dict):
        raise MediaSiteError("No video was found at that link.")
    return info


def _one_video(info: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a playlist result to its first entry."""
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise MediaSiteError("That link contains no playable video.")
        return entries[0]
    return info


def _check_playable(info: dict[str, Any]) -> None:
    if info.get("is_live"):
        raise MediaSiteError(
            "That is a live stream, which has no fixed length to extract from."
        )

    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        limit = settings.max_video_duration_seconds
        if duration > limit:
            raise MediaSiteError(
                f"That video is {int(duration)} seconds long, over the "
                f"{limit} second limit. Trim it and upload the file instead."
            )

    size = info.get("filesize") or info.get("filesize_approx")
    if isinstance(size, (int, float)) and size > settings.max_upload_size_bytes:
        raise MediaSiteError(
            f"The best available stream is about {int(size) // (1024 * 1024)} MB, "
            f"over the {settings.max_upload_size_mb} MB limit."
        )


def _saved_file(directory: Path) -> Path:
    """Pick the downloaded video out of the job directory, clearing leftovers."""
    written = [path for path in directory.glob("input.*") if path.is_file()]
    usable = [
        path for path in written if path.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS
    ]
    if not usable:
        for leftover in written:  # .part files from an aborted transfer
            leftover.unlink(missing_ok=True)
        raise MediaSiteError(
            "That video could not be downloaded within this server's limits."
        )

    chosen = max(usable, key=lambda path: path.stat().st_size)
    for leftover in written:
        if leftover != chosen:
            leftover.unlink(missing_ok=True)
    return chosen


def _display_name(info: dict[str, Any], path: Path) -> str:
    """A filename to show in the UI, built from the video's own title."""
    title = re.sub(r"\s+", " ", str(info.get("title") or "")).strip()[:120]
    return f"{title}{path.suffix}" if title else f"video{path.suffix}"


def _clear(directory: Path) -> None:
    """Drop anything a previous attempt left in the job directory."""
    for leftover in directory.glob("input.*"):
        leftover.unlink(missing_ok=True)


def _fetch_once(
    url: str, directory: Path, client: str | None
) -> tuple[Path, str]:
    """One full attempt with a single player client.

    Metadata is read first so an over-long or unplayable video is refused before
    anything is transferred. That costs a second extraction round-trip, which is
    a good trade against downloading a two-hour video only to reject it.
    """
    _clear(directory)
    _check_playable(
        _one_video(_extract(url, directory=directory, download=False, client=client))
    )

    info = _one_video(
        _extract(url, directory=directory, download=True, client=client)
    )
    path = _saved_file(directory)
    return path, _display_name(info, path)


def fetch(*, url: str, directory: Path) -> tuple[Path, str]:
    """Resolve and download a video from a media-site page.

    A bot check is the one failure worth retrying, and only with a different
    player client - the same one will be challenged again. Every other failure
    (private video, over the duration limit, no extractor) is final, so it
    propagates on the first attempt rather than provoking the site further.
    """
    if not settings.allow_media_site_urls:
        raise MediaSiteError(
            "Reading links from video sites is disabled on this server."
        )

    deadline = time.monotonic() + settings.media_site_timeout_seconds
    challenge: Challenged | None = None

    for client in (None, *CLIENT_FALLBACKS):
        # A challenged attempt can burn a lot of wall clock on its own retries,
        # so the budget is checked before spending more of it on the next one.
        if challenge is not None and time.monotonic() >= deadline:
            break
        try:
            return _fetch_once(url, directory, client)
        except Challenged as exc:
            challenge = exc

    _clear(directory)
    raise challenge if challenge else MediaSiteError(_GENERIC_ERROR)
