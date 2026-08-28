"""Video metadata and frame extraction, via FFmpeg's libraries in-process.

Decoding goes through PyAV, which binds libavformat/libavcodec/libavfilter
directly and ships them inside its wheel. That replaced shelling out to an
``ffmpeg`` binary, for one decisive reason: a serverless Python runtime has no
FFmpeg on PATH and no way to install one, so the subprocess approach could not
run there at all. A wheel is just a dependency.

It is also less machinery - no argument lists, no stderr pipe to drain, no
process to reap - and the filter graph is the same libavfilter the CLI drives,
so ``fps=`` and ``scale=`` behave as before.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass, asdict
from pathlib import Path

from app.config import settings

logger = logging.getLogger("video_to_image.video")

# Frames come out of the decoder as raw RGBA and go straight into Pillow, so no
# intermediate images are ever written to disk. Alpha is kept; Pillow flattens it
# when the chosen output format cannot store it.
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


def _av():
    """Import PyAV lazily so a broken install degrades instead of crashing."""
    try:
        import av
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VideoError(
            "Video processing is unavailable on this server (no video decoder)."
        ) from exc
    return av


def _open(source: Path):
    """Open a container, mapping any decoder error to a user-facing one."""
    av = _av()
    try:
        return av.open(str(source))
    except VideoError:
        raise
    except Exception as exc:  # PyAV raises a family of FFmpegError subclasses
        raise VideoError("This file could not be read as a video.") from exc


def _video_stream(container):
    stream = next((s for s in container.streams if s.type == "video"), None)
    if stream is None:
        raise VideoError("No video stream was found in this file.")
    # Let libav use threads; this is the whole decode budget on a small host.
    stream.thread_type = "AUTO"
    return stream


def ffmpeg_available() -> bool:
    """True when a decoder is present, i.e. PyAV imported.

    Named for the feature rather than the mechanism: /health, the page template
    and the tests all ask the same question - can this host convert a video? The
    answer used to depend on binaries on PATH and now depends on a wheel.
    """
    try:
        import av  # noqa: F401
    except ImportError:  # pragma: no cover - depends on the install
        return False
    return True


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
    with _open(path) as container:
        stream = _video_stream(container)
        codec = stream.codec_context

        # Container duration is authoritative; a stream's own is the fallback.
        duration = 0.0
        av = _av()
        if container.duration:
            duration = float(container.duration) / av.time_base
        elif stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)

        rate = stream.average_rate or stream.base_rate or 0
        name = original_filename or path.name

        return VideoInfo(
            filename=name,
            size_bytes=path.stat().st_size,
            duration=round(max(duration, 0.0), 3),
            width=int(codec.width or 0),
            height=int(codec.height or 0),
            fps=round(float(rate), 3),
            format_name=describe_format(
                container.format.name, name, dict(container.metadata or {})
            ),
            codec=codec.name or "unknown",
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


def _build_graph(stream, plan: ExtractionPlan, target_size, width: int, height: int):
    """The same libavfilter chain the CLI would get from ``-vf``.

    ``None`` when nothing needs filtering, so frames go straight from the decoder
    to Pillow.
    """
    if plan.fps is None and target_size is None:
        return None

    av = _av()
    graph = av.filter.Graph()
    last = graph.add_buffer(template=stream)
    if plan.fps is not None:
        node = graph.add("fps", f"{plan.fps:.6f}")
        last.link_to(node)
        last = node
    if target_size is not None:
        node = graph.add("scale", f"{width}:{height}:flags=lanczos")
        last.link_to(node)
        last = node
    last.link_to(graph.add("buffersink"))
    graph.configure()
    return graph


def _drain(graph):
    """Pull every frame the graph can currently produce."""
    av = _av()
    # EOFError once flushed, BlockingIOError while it still wants input.
    expected = (av.error.BlockingIOError, av.error.EOFError, av.error.FFmpegError)
    while True:
        try:
            yield graph.pull()
        except expected:
            return
        except (BlockingIOError, EOFError):  # pragma: no cover - older PyAV
            return


def _rgba_bytes(frame, width: int, height: int) -> bytes:
    """Packed RGBA for one frame, with libav's row padding removed.

    Each row is aligned, so a plane's buffer can be wider than the picture: a
    1921px frame carries a 7696-byte stride for 7684 bytes of pixels. Pillow
    wants the rows packed, so a padded plane is stitched row by row. PyAV's
    ``to_ndarray`` would do the same thing at the cost of depending on numpy,
    which installs to 61 MB - most of a serverless bundle's budget.
    """
    plane = frame.reformat(format=PIXEL_FORMAT, width=width, height=height).planes[0]
    row = width * BYTES_PER_PIXEL
    stride = plane.line_size
    view = memoryview(plane)
    if stride == row:
        return view.tobytes()
    return b"".join(view[i * stride : i * stride + row] for i in range(height))


def stream_frames(
    *,
    source: Path,
    plan: ExtractionPlan,
    source_size: tuple[int, int],
    target_size: tuple[int, int] | None = None,
) -> Iterator[tuple[int, float, bytes]]:
    """Yield ``(index, timestamp, rgba_bytes)`` for each extracted frame.

    Frames are decoded one at a time rather than written out as files: peak disk
    usage is zero and peak memory is a single frame, which is what makes a
    500-image limit safe even at high resolutions. Scaling happens in the filter
    graph, so only the final size is ever converted to RGBA.
    """
    width, height = target_size or source_size
    if width <= 0 or height <= 0:
        raise VideoError("This video has no usable picture dimensions.")

    deadline = time.monotonic() + settings.process_timeout_seconds
    emitted = 0

    with _open(source) as container:
        stream = _video_stream(container)
        graph = _build_graph(stream, plan, target_size, width, height)

        def emit(frame):
            nonlocal emitted
            data = _rgba_bytes(frame, width, height)
            index = emitted + 1
            emitted = index
            return index, round((index - 1) * plan.interval, 3), data

        try:
            for decoded in container.decode(stream):
                if time.monotonic() > deadline:
                    raise VideoError(
                        "Processing took too long and was stopped. "
                        "Try a shorter video or fewer images."
                    )
                if graph is None:
                    yield emit(decoded)
                    if emitted >= plan.count:
                        return
                    continue

                graph.push(decoded)
                for filtered in _drain(graph):
                    yield emit(filtered)
                    if emitted >= plan.count:
                        return

            # Flush: the fps filter can be holding the last frame back.
            if graph is not None and emitted < plan.count:
                try:
                    graph.push(None)
                except Exception:  # pragma: no cover - nothing buffered
                    pass
                for filtered in _drain(graph):
                    yield emit(filtered)
                    if emitted >= plan.count:
                        return
        except VideoError:
            raise
        except Exception as exc:  # a truncated or corrupt file surfaces here
            if emitted == 0:
                logger.warning("Decoding failed: %s", str(exc)[:500])
                raise VideoError("No frames could be extracted from this video.") from exc
            # Partial output is still usable; stop where the file stops.

    if emitted == 0:
        raise VideoError("No frames could be extracted from this video.")


def frame_size(
    source_size: tuple[int, int], target_size: tuple[int, int] | None
) -> tuple[int, int]:
    """The pixel size of the frames ``stream_frames`` will yield."""
    return target_size or source_size
