"""FFmpeg / ffprobe integration: metadata and frame extraction.

Subprocesses are always invoked with an argument list (never ``shell=True``) and
always under a wall-clock timeout.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, asdict
from pathlib import Path

from app.config import settings

logger = logging.getLogger("video_to_image.video")

# Frames leave FFmpeg as raw RGBA and go straight into Pillow, so no intermediate
# images are ever written to disk. Alpha is kept; Pillow flattens it when the
# chosen output format cannot store it.
BYTES_PER_PIXEL = 4
PIXEL_FORMAT = "rgba"


class VideoError(Exception):
    """A user-facing problem with a video file or with processing it."""


@dataclass(frozen=True)
class VideoInfo:
    filename: str
    size_bytes: int
    duration: float
    width: int
    height: int
    fps: float
    format_name: str
    codec: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionPlan:
    """A resolved extraction request: what ffmpeg should do, and how many frames."""

    method: str
    fps: float | None  # None => keep every decoded frame
    count: int  # hard cap on emitted frames
    interval: float  # seconds between consecutive frames (for timestamps)
    truncated: bool  # True when a safety limit reduced the request


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed binary, list args, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.process_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise VideoError(
            "Video processing is unavailable on this server (FFmpeg not found)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoError(
            "Processing took too long and was stopped. Try a shorter video or fewer images."
        ) from exc


def ffmpeg_available() -> bool:
    """True when both binaries can be resolved."""
    return bool(
        shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path)
    )


# FFmpeg reports one demuxer name for a whole container family, so an MP4 comes
# back as "mov,mp4,m4a,3gp,3g2,mj2" and a WebM as "matroska,webm". Naming the
# actual container needs the file's own extension as a tie-breaker.
_FORMAT_LABELS = {
    "mp4": "MP4",
    "m4v": "M4V",
    "mov": "MOV",
    "webm": "WebM",
    "matroska": "MKV",
    "mkv": "MKV",
    "avi": "AVI",
    "mpeg": "MPEG",
    "mpg": "MPEG",
    "mpegts": "MPEG-TS",
    "flv": "FLV",
    "asf": "WMV",
    "ogg": "OGG",
    "3gp": "3GP",
}

# Extensions whose container is part of a differently-named demuxer family.
_FORMAT_FAMILIES = {"m4v": "mp4", "mkv": "matroska", "mpg": "mpeg"}

# Within the shared mov/mp4 demuxer the file's own "major brand" says which
# container it actually is - which beats trusting the extension, since a
# QuickTime file named .mp4 still reports brand "qt  ".
_MP4_BRANDS = {
    "qt": "MOV",
    "isom": "MP4",
    "iso2": "MP4",
    "iso4": "MP4",
    "iso5": "MP4",
    "iso6": "MP4",
    "mp41": "MP4",
    "mp42": "MP4",
    "avc1": "MP4",
    "dash": "MP4",
    "mmp4": "MP4",
    "msnv": "MP4",
    "m4v": "M4V",
    "m4vh": "M4V",
    "m4vp": "M4V",
    "3gp4": "3GP",
    "3gp5": "3GP",
    "3g2a": "3GP",
    "mj2s": "MJ2",
}


def describe_format(
    format_name: str | None, filename: str, tags: dict | None = None
) -> str:
    """Name the container the way a person would, not the way FFmpeg groups it."""
    tokens = [token.strip().lower() for token in (format_name or "").split(",")]
    tokens = [token for token in tokens if token]
    extension = Path(filename).suffix.lstrip(".").lower()

    if "mp4" in tokens or "mov" in tokens:
        brand = str((tags or {}).get("major_brand") or "").strip().lower()
        label = _MP4_BRANDS.get(brand)
        if label:
            return label

    if extension and (
        extension in tokens or _FORMAT_FAMILIES.get(extension) in tokens
    ):
        return _FORMAT_LABELS.get(extension, extension.upper())

    if tokens:
        return _FORMAT_LABELS.get(tokens[0], tokens[0].upper())
    if extension:
        return _FORMAT_LABELS.get(extension, extension.upper())
    return "Unknown"


def _parse_fps(rate: str | None) -> float:
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_value = float(den)
            return float(num) / den_value if den_value else 0.0
        return float(rate)
    except (TypeError, ValueError):
        return 0.0


def probe(path: Path, *, original_filename: str | None = None) -> VideoInfo:
    """Read metadata from a media file, verifying it contains a video stream."""
    result = _run(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise VideoError("This file could not be read as a video.")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoError("This file could not be read as a video.") from exc

    streams = data.get("streams") or []
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise VideoError("No video stream was found in this file.")

    container = data.get("format") or {}
    duration = 0.0
    for candidate in (container.get("duration"), video_stream.get("duration")):
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break

    fps = _parse_fps(video_stream.get("avg_frame_rate")) or _parse_fps(
        video_stream.get("r_frame_rate")
    )

    name = original_filename or path.name
    return VideoInfo(
        filename=name,
        size_bytes=path.stat().st_size,
        duration=round(max(duration, 0.0), 3),
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        fps=round(fps, 3),
        format_name=describe_format(
            container.get("format_name"), name, container.get("tags")
        ),
        codec=video_stream.get("codec_name") or "unknown",
    )


def resolve_plan(
    *,
    method: str,
    info: VideoInfo,
    fps: float | None = None,
    count: int | None = None,
    interval: float | None = None,
) -> ExtractionPlan:
    """Turn a user request into a bounded extraction plan."""
    return _single_frame(
        _resolve_plan(
            method=method, info=info, fps=fps, count=count, interval=interval
        )
    )


def _resolve_plan(
    *,
    method: str,
    info: VideoInfo,
    fps: float | None,
    count: int | None,
    interval: float | None,
) -> ExtractionPlan:
    limit = settings.max_output_images
    duration = info.duration or 0.0
    source_fps = info.fps if info.fps > 0 else 25.0

    if method == "every_frame":
        total = math.ceil(duration * source_fps) if duration else limit
        total = max(total, 1)
        return ExtractionPlan(
            method=method,
            fps=None,
            count=min(total, limit),
            interval=1 / source_fps,
            truncated=total > limit,
        )

    if method == "fps":
        # `or` would swallow an explicit 0, which must be an error, not a default.
        value = float(1 if fps is None else fps)
        if not 0.01 <= value <= 120:
            raise VideoError("Frame rate must be between 0.01 and 120 fps.")
        total = max(math.ceil(duration * value), 1) if duration else limit
        return ExtractionPlan(
            method=method,
            fps=value,
            count=min(total, limit),
            interval=1 / value,
            truncated=total > limit,
        )

    if method == "count":
        requested = int(1 if count is None else count)
        if requested < 1:
            raise VideoError("Number of images must be at least 1.")
        wanted = min(requested, limit)
        if duration > 0:
            step = duration / wanted
            value = 1 / step
        else:  # pragma: no cover - duration is validated on upload
            step = 1 / source_fps
            value = source_fps
        return ExtractionPlan(
            method=method,
            fps=value,
            count=wanted,
            interval=step,
            truncated=requested > limit,
        )

    if method == "interval":
        seconds = float(1 if interval is None else interval)
        if not 0.05 <= seconds <= 3600:
            raise VideoError("Interval must be between 0.05 and 3600 seconds.")
        total = max(math.ceil(duration / seconds), 1) if duration else limit
        return ExtractionPlan(
            method=method,
            fps=1 / seconds,
            count=min(total, limit),
            interval=seconds,
            truncated=total > limit,
        )

    raise VideoError("Unknown frame extraction method.")


def _single_frame(plan: ExtractionPlan) -> ExtractionPlan:
    """Take the first decoded frame instead of filtering for one.

    A very low `fps` filter (say one frame every 30s of a 10s clip) can emit
    nothing at all, which would surface as "no frames could be extracted" when
    the honest answer is a single frame from the start of the video.
    """
    if plan.count != 1 or plan.fps is None:
        return plan
    return ExtractionPlan(
        method=plan.method,
        fps=None,
        count=1,
        interval=plan.interval,
        truncated=plan.truncated,
    )


def _read_exact(stream, size: int) -> bytes:
    """Read exactly ``size`` bytes, or fewer at the end of the stream."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def stream_frames(
    *,
    source: Path,
    plan: ExtractionPlan,
    source_size: tuple[int, int],
    target_size: tuple[int, int] | None = None,
) -> Iterator[tuple[int, float, bytes]]:
    """Yield ``(index, timestamp, rgba_bytes)`` for each extracted frame.

    Frames are piped from FFmpeg one at a time rather than written out as files:
    peak disk usage is zero and peak memory is a single frame, which is what
    makes a 500-image limit safe even at high resolutions. Scaling is done by
    FFmpeg so only the final size is ever decoded.
    """
    width, height = target_size or source_size
    if width <= 0 or height <= 0:
        raise VideoError("This video has no usable picture dimensions.")
    frame_bytes = width * height * BYTES_PER_PIXEL

    filters: list[str] = []
    if plan.fps is not None:
        filters.append(f"fps={plan.fps:.6f}")
    if target_size is not None:
        filters.append(f"scale={width}:{height}:flags=lanczos")

    cmd = [
        settings.ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += [
        "-an",
        "-sn",
        "-frames:v",
        str(plan.count),
        "-f",
        "rawvideo",
        "-pix_fmt",
        PIXEL_FORMAT,
        "-",
    ]

    deadline = time.monotonic() + settings.process_timeout_seconds
    # A real file for stderr: a pipe we are not draining could deadlock FFmpeg.
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed binary, list args, no shell
                cmd,
                stdout=subprocess.PIPE,
                stderr=errors,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:  # pragma: no cover - environment dependent
            raise VideoError(
                "Video processing is unavailable on this server (FFmpeg not found)."
            ) from exc

        emitted = 0
        try:
            while emitted < plan.count:
                if time.monotonic() > deadline:
                    raise VideoError(
                        "Processing took too long and was stopped. "
                        "Try a shorter video or fewer images."
                    )
                data = _read_exact(process.stdout, frame_bytes)
                if len(data) < frame_bytes:
                    break
                yield emitted + 1, round(emitted * plan.interval, 3), data
                emitted += 1
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover - stuck ffmpeg
                    process.kill()
                    process.wait()

        if emitted == 0:
            errors.seek(0)
            detail = errors.read().decode("utf-8", "replace").strip()
            if detail:
                logger.warning("FFmpeg produced no frames: %s", detail[:500])
            raise VideoError("No frames could be extracted from this video.")


def frame_size(
    source_size: tuple[int, int], target_size: tuple[int, int] | None
) -> tuple[int, int]:
    """The pixel size of the frames ``stream_frames`` will yield."""
    return target_size or source_size
