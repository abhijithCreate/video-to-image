from __future__ import annotations

import pytest
from PIL import Image

from app.services import image as image_service


def test_presets_override_custom_quality():
    options = image_service.resolve_options(fmt="jpg", quality_preset="low", quality=99)
    assert options.quality == 50


def test_custom_quality_is_used_when_preset_is_custom():
    options = image_service.resolve_options(fmt="webp", quality_preset="custom", quality=33)
    assert options.quality == 33
    assert options.supports_quality is True


def test_unknown_format_is_rejected():
    with pytest.raises(image_service.ImageError):
        image_service.resolve_options(fmt="svg")


def test_out_of_range_quality_is_rejected():
    with pytest.raises(image_service.ImageError):
        image_service.resolve_options(fmt="jpg", quality_preset="custom", quality=0)


def test_oversized_dimensions_are_rejected():
    with pytest.raises(image_service.ImageError):
        image_service.resolve_options(fmt="png", width=99999)


def test_aspect_ratio_is_preserved_by_default():
    options = image_service.resolve_options(fmt="jpg", width=1280, height=720)
    assert image_service.compute_target_size((1920, 800), options) == (1280, 533)


def test_exact_size_when_aspect_ratio_is_not_maintained():
    options = image_service.resolve_options(
        fmt="jpg", width=1280, height=720, maintain_aspect=False
    )
    assert image_service.compute_target_size((1920, 800), options) == (1280, 720)


def test_original_size_returns_none():
    options = image_service.resolve_options(fmt="jpg")
    assert image_service.compute_target_size((1920, 1080), options) is None


def test_open_frame_reads_raw_rgba():
    size = (4, 2)
    data = bytes(bytearray([1, 2, 3, 255]) * (size[0] * size[1]))
    frame = image_service.open_frame(data, size)
    assert frame.size == size
    assert frame.mode == "RGBA"
    assert frame.getpixel((0, 0)) == (1, 2, 3, 255)


def test_open_frame_rejects_truncated_data():
    with pytest.raises(image_service.ImageError):
        image_service.open_frame(b"\x00" * 8, (64, 64))


@pytest.mark.parametrize("fmt", ["jpg", "png", "webp", "bmp", "tiff", "gif"])
def test_every_format_encodes(tmp_path, sample_frame, fmt):
    options = image_service.resolve_options(fmt=fmt, quality_preset="high")
    destination = tmp_path / f"out{options.extension}"
    meta = image_service.save(
        frame=sample_frame, destination=destination, options=options
    )

    assert destination.is_file()
    assert meta["size_bytes"] > 0
    assert (meta["width"], meta["height"]) == (640, 360)
    with Image.open(destination) as written:
        assert written.format == options.pillow_format


def test_alpha_is_flattened_for_jpeg(tmp_path, sample_frame):
    options = image_service.resolve_options(fmt="jpg")
    destination = tmp_path / "out.jpg"
    image_service.save(frame=sample_frame, destination=destination, options=options)
    with Image.open(destination) as written:
        assert written.mode == "RGB"


def test_resize_is_applied(tmp_path, sample_frame):
    options = image_service.resolve_options(fmt="png", width=320)
    target = image_service.compute_target_size((640, 360), options)
    meta = image_service.save(
        frame=sample_frame,
        destination=tmp_path / "small.png",
        options=options,
        target_size=target,
    )
    assert (meta["width"], meta["height"]) == (320, 180)


def test_thumbnail_fits_inside_the_longest_edge(tmp_path, sample_frame):
    destination = tmp_path / "thumb.jpg"
    image_service.save_thumbnail(
        frame=sample_frame, destination=destination, longest_edge=160
    )
    with Image.open(destination) as thumb:
        assert max(thumb.size) <= 160
        assert thumb.format == "JPEG"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Beach Sunset", "Beach-Sunset"),
        ("  spaced  ", "spaced"),
        ("my_clip", "my_clip"),
        ("a___b", "a_b"),
        ("-lead-and-trail-", "lead-and-trail"),
        ("holiday.jpg", "holiday-jpg"),
        ("café", "caf"),
        # Traversal is folded away rather than rejected.
        ("../../etc/passwd", "etc-passwd"),
        # Nothing usable left means "no title given".
        ("!!!", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_titles_are_reduced_to_safe_stems(raw, expected):
    assert image_service.sanitise_title(raw) == expected


def test_long_titles_are_truncated_without_a_trailing_separator():
    # Truncation lands on the folded space, which is then trimmed away.
    title = image_service.sanitise_title("a" * 59 + " " + "b" * 20)
    assert title == "a" * 59
    assert len(title) <= image_service.MAX_TITLE_LENGTH
    assert not title.endswith(("-", "_"))


def test_long_titles_keep_the_full_allowance_when_nothing_is_trimmed():
    title = image_service.sanitise_title("b" * 200)
    assert title == "b" * image_service.MAX_TITLE_LENGTH


def test_frame_name_uses_the_title_and_format():
    options = image_service.resolve_options(fmt="png", title="Beach Sunset")
    assert options.frame_name(1) == "Beach-Sunset_00001.png"
    assert options.frame_name(42) == "Beach-Sunset_00042.png"


def test_frame_name_falls_back_to_frame_without_a_title():
    options = image_service.resolve_options(fmt="jpg")
    assert options.title == ""
    assert options.stem == image_service.DEFAULT_TITLE
    assert options.frame_name(7) == "frame_00007.jpg"


@pytest.mark.parametrize(
    "raw", ["../../etc/passwd", "!!!", "  ", "ééé", "a" * 200, "..", "-"]
)
def test_generated_names_are_always_servable(raw):
    """Whatever the user types, the name must satisfy jobs.SAFE_NAME_RE."""
    from app.jobs import SAFE_NAME_RE

    options = image_service.resolve_options(fmt="tiff", title=raw)
    assert SAFE_NAME_RE.match(options.frame_name(99999))
