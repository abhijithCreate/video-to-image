"""Pillow-based encoding: format conversion, quality, resize, compression."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import QUALITY_PRESETS


class ImageError(Exception):
    """A user-facing problem with image conversion."""


# name -> (pillow format, extension, supports lossy quality, supports alpha)
FORMATS: dict[str, tuple[str, str, bool, bool]] = {
    "jpg": ("JPEG", ".jpg", True, False),
    "jpeg": ("JPEG", ".jpeg", True, False),
    "png": ("PNG", ".png", False, True),
    "webp": ("WEBP", ".webp", True, True),
    "bmp": ("BMP", ".bmp", False, False),
    "tiff": ("TIFF", ".tiff", False, True),
    "gif": ("GIF", ".gif", False, True),
}

MAX_DIMENSION = 7680

# Generated names are "<title>_00001.<ext>", and they are served back through
# app.jobs.resolve_file, which only accepts [A-Za-z0-9][A-Za-z0-9._-]{0,127}.
# 60 characters leaves ample room for the counter and the longest extension.
MAX_TITLE_LENGTH = 60
DEFAULT_TITLE = "frame"

_UNSAFE_TITLE_RE = re.compile(r"[^A-Za-z0-9_-]+")
_REPEATED_SEPARATOR_RE = re.compile(r"([_-])[_-]+")


@dataclass(frozen=True)
class ImageOptions:
    fmt: str = "jpg"
    quality: int = 85
    width: int | None = None
    height: int | None = None
    maintain_aspect: bool = True
    # Empty means the user named nothing, so images fall back to DEFAULT_TITLE
    # and the archive keeps its job-derived name.
    title: str = ""

    @property
    def pillow_format(self) -> str:
        return FORMATS[self.fmt][0]

    @property
    def extension(self) -> str:
        return FORMATS[self.fmt][1]

    @property
    def supports_quality(self) -> bool:
        return FORMATS[self.fmt][2]

    @property
    def supports_alpha(self) -> bool:
        return FORMATS[self.fmt][3]

    @property
    def stem(self) -> str:
        """Base name shared by every generated image."""
        return self.title or DEFAULT_TITLE

    def frame_name(self, index: int) -> str:
        """Filename for one frame, e.g. ``beach-sunset_00001.jpg``."""
        return f"{self.stem}_{index:05d}{self.extension}"


def sanitise_title(title: str | None) -> str:
    """Reduce a user-supplied title to a safe filename stem.

    Returns ``""`` when nothing usable remains, which callers read as "no title
    given". Unusable characters are folded to a hyphen rather than rejected: a
    title is a convenience, not something worth failing a conversion over.
    """
    folded = _UNSAFE_TITLE_RE.sub("-", (title or "").strip())
    collapsed = _REPEATED_SEPARATOR_RE.sub(r"\1", folded)
    # Trim separators twice: truncation can expose a fresh trailing one, and the
    # leading character has to be alphanumeric for the name to be servable.
    return collapsed.strip("_-")[:MAX_TITLE_LENGTH].strip("_-")


def resolve_options(
    *,
    fmt: str,
    quality_preset: str | None = None,
    quality: int | None = None,
    width: int | None = None,
    height: int | None = None,
    maintain_aspect: bool = True,
    title: str | None = None,
) -> ImageOptions:
    """Validate and normalise a conversion request."""
    key = (fmt or "").strip().lower()
    if key not in FORMATS:
        raise ImageError("Unsupported image format.")

    preset = (quality_preset or "custom").strip().lower()
    if preset in QUALITY_PRESETS:
        resolved_quality = QUALITY_PRESETS[preset]
    else:
        resolved_quality = 85 if quality is None else int(quality)
    if not 1 <= resolved_quality <= 100:
        raise ImageError("Quality must be between 1 and 100.")

    for value, label in ((width, "Width"), (height, "Height")):
        if value is not None and not 1 <= int(value) <= MAX_DIMENSION:
            raise ImageError(f"{label} must be between 1 and {MAX_DIMENSION} pixels.")

    return ImageOptions(
        fmt=key,
        quality=resolved_quality,
        width=int(width) if width else None,
        height=int(height) if height else None,
        maintain_aspect=bool(maintain_aspect),
        title=sanitise_title(title),
    )


def compute_target_size(
    source: tuple[int, int], options: ImageOptions
) -> tuple[int, int] | None:
    """Resolve the output size, or ``None`` to keep the source size.

    With ``maintain_aspect`` the requested box is treated as a bound the image is
    fitted inside; otherwise the requested dimensions are used exactly.
    """
    src_w, src_h = source
    if src_w <= 0 or src_h <= 0:
        return None
    if options.width is None and options.height is None:
        return None

    if not options.maintain_aspect and options.width and options.height:
        target = (options.width, options.height)
    else:
        ratio_w = options.width / src_w if options.width else None
        ratio_h = options.height / src_h if options.height else None
        ratios = [r for r in (ratio_w, ratio_h) if r is not None]
        ratio = min(ratios)
        target = (max(round(src_w * ratio), 1), max(round(src_h * ratio), 1))

    return target if target != (src_w, src_h) else None


def _prepare(image: Image.Image, options: ImageOptions) -> Image.Image:
    if options.pillow_format == "GIF":
        return image.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE)
    if options.supports_alpha:
        return image if image.mode in {"RGB", "RGBA", "L"} else image.convert("RGBA")
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.split()[-1])
        return flattened
    return image.convert("RGB")


def _save_params(options: ImageOptions) -> dict:
    fmt = options.pillow_format
    if fmt == "JPEG":
        return {
            "quality": options.quality,
            "optimize": True,
            "progressive": options.quality >= 85,
            "subsampling": 0 if options.quality >= 90 else 2,
        }
    if fmt == "WEBP":
        return {"quality": options.quality, "method": 4}
    if fmt == "PNG":
        # PNG is lossless: the quality dial maps to zlib effort, not fidelity.
        # `optimize` (zlib level 9) measured 3x slower for 0.4% smaller files on
        # real video frames, which is a bad trade across a 500-image batch.
        return {"compress_level": 6}
    if fmt == "TIFF":
        return {"compression": "tiff_lzw"}
    if fmt == "GIF":
        return {"optimize": True}
    return {}


def open_frame(data: bytes, size: tuple[int, int]) -> Image.Image:
    """Wrap raw RGBA bytes from FFmpeg as a Pillow image (no copy of the data)."""
    try:
        return Image.frombuffer("RGBA", size, data, "raw", "RGBA", 0, 1)
    except (OSError, ValueError) as exc:
        raise ImageError("A frame could not be read.") from exc


def save(
    *,
    frame: Image.Image,
    destination: Path,
    options: ImageOptions,
    target_size: tuple[int, int] | None = None,
) -> dict:
    """Encode one frame and return its metadata.

    ``target_size`` is normally already applied upstream by FFmpeg; resizing here
    is a safety net for the case where the decoded size differs.
    """
    try:
        image = frame
        if target_size is not None and target_size != image.size:
            image = image.resize(target_size, Image.Resampling.LANCZOS)
        prepared = _prepare(image, options)
        destination.parent.mkdir(parents=True, exist_ok=True)
        prepared.save(destination, options.pillow_format, **_save_params(options))
        out_w, out_h = prepared.size
    except OSError as exc:
        raise ImageError("A frame could not be converted.") from exc

    return {
        "filename": destination.name,
        "width": out_w,
        "height": out_h,
        "size_bytes": destination.stat().st_size,
        "format": options.fmt,
    }


def save_thumbnail(
    *, frame: Image.Image, destination: Path, longest_edge: int
) -> None:
    """Write a small JPEG preview so the results grid stays light."""
    try:
        preview = frame.convert("RGB")
        preview.thumbnail((longest_edge, longest_edge), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        preview.save(destination, "JPEG", quality=75, optimize=True)
    except OSError as exc:
        raise ImageError("A preview could not be generated.") from exc
