"""Fetching a video from a user-supplied URL.

A URL typed by a stranger is a request for this server to make an outbound
connection, so every hop is validated before it is followed: only http/https,
only public addresses (unless explicitly allowed otherwise), redirects checked
one at a time, and the response body capped and timed while it streams.

Two kinds of link arrive here. One *is* the video file and is streamed straight
to disk by this module. The other is a page that merely plays a video - a
YouTube watch URL, say - which is handed to app.services.media_site to resolve.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import ALLOWED_VIDEO_EXTENSIONS, settings
from app.services import media_site

CHUNK_SIZE = 256 * 1024
USER_AGENT = "video-to-image/1.0 (+https://github.com/)"

# Some servers send a generic type, so this is a hint rather than a gate.
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/avi": ".avi",
    "video/x-matroska": ".mkv",
    "video/mpeg": ".mpeg",
    "video/x-m4v": ".m4v",
}


class DownloadError(Exception):
    """A user-facing problem fetching a video from a URL."""


class NotADirectVideo(DownloadError):
    """The link responded, but with a page rather than a video file.

    Raised so :func:`fetch` can tell "this is not a video" apart from a genuine
    transport failure, and only fall back to the extractor for the former.
    """


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Turn redirects into errors so each hop can be validated by hand."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def _assert_public_host(url: str) -> None:
    """Refuse URLs that resolve to anything but a public address."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise DownloadError("Only http:// and https:// links are supported.")
    if not parts.hostname:
        raise DownloadError("That link is not a valid URL.")
    if settings.allow_private_url_hosts:
        return

    try:
        resolved = socket.getaddrinfo(parts.hostname, parts.port or 0)
    except (socket.gaierror, UnicodeError) as exc:
        raise DownloadError("That host could not be found.") from exc

    for info in resolved:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise DownloadError(
                "That link points to a private address, which is not allowed."
            )


def _extension_for(url: str, content_type: str | None) -> str:
    """Pick the file extension from the URL, falling back to the content type."""
    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    extension = Path(name).suffix.lower()
    if extension in ALLOWED_VIDEO_EXTENSIONS:
        return extension

    base_type = (content_type or "").split(";")[0].strip().lower()
    mapped = CONTENT_TYPE_EXTENSIONS.get(base_type)
    if mapped:
        return mapped

    raise NotADirectVideo(
        "That link does not look like a video file. Use a direct link ending in "
        + ", ".join(sorted(e.lstrip(".") for e in ALLOWED_VIDEO_EXTENSIONS))
        + "."
    )


def _open(url: str, deadline: float):
    """Open a URL, following a bounded number of validated redirects."""
    for _ in range(settings.url_max_redirects + 1):
        _assert_public_host(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DownloadError("That link took too long to respond.")

        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return _opener.open(request, timeout=min(remaining, 30)), url
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code in {301, 302, 303, 307, 308} and location:
                url = urllib.parse.urljoin(url, location)
                exc.close()
                continue
            exc.close()
            raise DownloadError(
                f"The server returned an error ({exc.code}) for that link."
            ) from exc
        except urllib.error.URLError as exc:
            raise DownloadError("That link could not be reached.") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise DownloadError("That link took too long to respond.") from exc

    raise DownloadError("That link redirects too many times.")


def _fetch_direct(url: str, directory: Path) -> tuple[Path, str]:
    """Stream a URL that is itself a video file into ``directory``.

    Returns the saved path and a display filename. The transfer is stopped as
    soon as it exceeds the upload size limit, so an oversized or endless
    response cannot fill the disk.
    """
    deadline = time.monotonic() + settings.url_fetch_timeout_seconds
    response, final_url = _open(url, deadline)

    with response:
        content_type = response.headers.get("Content-Type")
        extension = _extension_for(final_url, content_type)

        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit():
            if int(declared) > settings.max_upload_size_bytes:
                raise DownloadError(
                    f"That video is larger than the {settings.max_upload_size_mb} MB limit."
                )

        destination = directory / f"input{extension}"
        written = 0
        with destination.open("wb") as handle:
            while True:
                if time.monotonic() > deadline:
                    raise DownloadError("That download took too long and was stopped.")
                try:
                    chunk = response.read(CHUNK_SIZE)
                except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                    raise DownloadError("That download failed part-way through.") from exc
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.max_upload_size_bytes:
                    raise DownloadError(
                        f"That video is larger than the {settings.max_upload_size_mb} MB limit."
                    )
                handle.write(chunk)

    if written == 0:
        raise DownloadError("That link returned an empty file.")

    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(final_url).path)).name
    display_name = name if Path(name).suffix.lower() == extension else f"video{extension}"
    return destination, display_name


def _fetch_via_extractor(url: str, directory: Path) -> tuple[Path, str]:
    """Hand a page URL to yt-dlp. Raises :class:`media_site.MediaSiteError`."""
    # The extractor does its own fetching, so validate the host we were given
    # before handing control over.
    _assert_public_host(url)
    return media_site.fetch(url=url, directory=directory)


def fetch(*, url: str, directory: Path) -> tuple[Path, str]:
    """Download a video into ``directory`` from any supported kind of link.

    A direct link to a video file is streamed here. Anything else - a YouTube
    watch page, for instance - is resolved by the extractor, either because the
    host is a known media site or because the direct attempt came back with a
    page instead of a video.
    """
    if not settings.allow_url_uploads:
        raise DownloadError("Fetching videos from a URL is disabled on this server.")

    url = (url or "").strip()
    if not url:
        raise DownloadError("Enter a video link.")
    if len(url) > 2048:
        raise DownloadError("That link is too long.")

    if media_site.is_media_site_url(url):
        if not media_site.enabled():
            # Otherwise this falls through to the direct path and comes back as
            # "that does not look like a video file", which reads as though the
            # user mistyped the link.
            raise DownloadError(
                "Links from video sites are not supported on this server. "
                "Download the video yourself and upload the file."
            )
        try:
            return _fetch_via_extractor(url, directory)
        except media_site.MediaSiteError as exc:
            raise DownloadError(str(exc)) from exc

    try:
        return _fetch_direct(url, directory)
    except NotADirectVideo as not_a_video:
        if not media_site.enabled():
            raise
        try:
            return _fetch_via_extractor(url, directory)
        except media_site.UnsupportedSite:
            # No extractor recognised it either, so the plainer "that is not a
            # video file" message is the more useful one to show.
            raise not_a_video from None
        except media_site.MediaSiteError as exc:
            raise DownloadError(str(exc)) from exc
