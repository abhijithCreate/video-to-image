"""Application configuration, loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"

    max_upload_size_mb: int = 50
    max_video_duration_seconds: int = 60
    max_output_images: int = 500

    job_retention_minutes: int = 60
    temp_dir: Path = Path("/tmp/video-to-image")

    # Ceiling on the total bytes one job may produce. 500 lossless images at high
    # resolution can run to several GB, so this is what keeps the limit safe.
    max_total_output_mb: int = 2048

    # A result is destroyed once this many distinct clients have downloaded it,
    # so a result URL cannot be shared or hot-linked.
    max_download_clients: int = 3

    # Fetching a video from a URL the user pastes in.
    allow_url_uploads: bool = True
    url_fetch_timeout_seconds: int = 60
    url_max_redirects: int = 3
    # Off by default: allowing private hosts turns this server into an SSRF
    # proxy for its own network. Enable only for a trusted internal deployment.
    allow_private_url_hosts: bool = False

    # Resolving a video from a media-site page (YouTube and similar) via yt-dlp.
    # This is a fallback for links that are not the video file itself.
    allow_media_site_urls: bool = True
    # Tallest stream the extractor will pick. Frame extraction rarely needs more
    # than 1080p, and capping it keeps the download inside the upload limit.
    media_site_max_height: int = 1080
    # Total budget for resolving one page link, across every client attempted.
    # Larger than url_fetch_timeout_seconds because the extractor makes several
    # requests of its own before a byte of video moves.
    media_site_timeout_seconds: int = 120
    # Optional credentials for when a site's bot check will not let up: a
    # cookies.txt export (Netscape format), or a browser to read cookies from
    # ("chrome", "firefox", "safari", ...). Both are off by default - sending a
    # logged-in session to a video site is a decision for the operator.
    youtube_cookies_file: Path | None = None
    youtube_cookies_from_browser: str = ""

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    process_timeout_seconds: int = 600

    # Longest edge of a grid thumbnail, in pixels.
    thumbnail_size: int = 320

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_total_output_bytes(self) -> int:
        return self.max_total_output_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def retention_label(self) -> str:
        """Human phrasing of the retention window, for UI copy."""
        minutes = self.job_retention_minutes
        if minutes >= 60 and minutes % 60 == 0:
            hours = minutes // 60
            return "1 hour" if hours == 1 else f"{hours} hours"
        return f"{minutes} minute" + ("" if minutes == 1 else "s")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Formats accepted on upload. Extensions are a first gate only; ffprobe has the
# final say on whether a file really is a decodable video.
ALLOWED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".webm", ".mov", ".avi", ".mkv", ".mpeg", ".mpg", ".m4v"}
)

QUALITY_PRESETS: dict[str, int] = {
    "low": 50,
    "medium": 70,
    "high": 85,
    "very_high": 95,
}
