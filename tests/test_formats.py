"""The container label shown in the UI, which FFmpeg does not give us directly."""

from __future__ import annotations

import pytest

from app.services.video import describe_format

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
MKV_FAMILY = "matroska,webm"


@pytest.mark.parametrize(
    ("format_name", "filename", "expected"),
    [
        # The demuxer name alone would call every one of these "MOV".
        (MP4_FAMILY, "clip.mp4", "MP4"),
        (MP4_FAMILY, "clip.MP4", "MP4"),
        (MP4_FAMILY, "clip.mov", "MOV"),
        (MP4_FAMILY, "clip.m4v", "M4V"),
        # ...and both of these "MATROSKA".
        (MKV_FAMILY, "clip.webm", "WebM"),
        (MKV_FAMILY, "clip.mkv", "MKV"),
        ("avi", "clip.avi", "AVI"),
        ("mpeg", "clip.mpeg", "MPEG"),
        ("mpeg", "clip.mpg", "MPEG"),
        ("mpegts", "clip.ts", "MPEG-TS"),
        ("asf", "clip.wmv", "WMV"),
        # Unknown or missing pieces still produce something sensible.
        (MP4_FAMILY, "clip", "MOV"),
        (None, "clip.mp4", "MP4"),
        ("", "", "Unknown"),
        ("weirdformat", "clip.xyz", "WEIRDFORMAT"),
    ],
)
def test_container_is_named_the_way_a_person_would(format_name, filename, expected):
    assert describe_format(format_name, filename) == expected


@pytest.mark.parametrize(
    ("filename", "brand", "expected"),
    [
        # The brand is the file's own statement of what it is.
        ("clip.mp4", "isom", "MP4"),
        ("clip.mp4", "mp42", "MP4"),
        ("GEMINI_GENERATED_VIDEO_18D1DA7B.MP4", "isom", "MP4"),
        ("clip.mov", "qt  ", "MOV"),
        ("clip.m4v", "M4V ", "M4V"),
        ("clip.3gp", "3gp4", "3GP"),
        # A QuickTime file handed over with an .mp4 name is still QuickTime.
        ("mislabelled.mp4", "qt  ", "MOV"),
        # An MP4 named .mov is really an MP4.
        ("mislabelled.mov", "isom", "MP4"),
        # An unrecognised brand falls back to the extension.
        ("clip.mp4", "zzzz", "MP4"),
        ("clip.mov", "", "MOV"),
    ],
)
def test_major_brand_identifies_the_real_container(filename, brand, expected):
    assert (
        describe_format(MP4_FAMILY, filename, {"major_brand": brand}) == expected
    )


def test_brand_is_ignored_outside_the_mp4_family():
    # A stray brand tag must not relabel a Matroska file.
    assert describe_format(MKV_FAMILY, "clip.mkv", {"major_brand": "isom"}) == "MKV"
