from __future__ import annotations

import pytest

from app.config import settings
from app.services import video as video_service


def info(duration: float = 10.0, fps: float = 30.0) -> video_service.VideoInfo:
    return video_service.VideoInfo(
        filename="clip.mp4",
        size_bytes=1024,
        duration=duration,
        width=1920,
        height=1080,
        fps=fps,
        format_name="mov",
        codec="h264",
    )


def test_fps_plan_counts_frames_across_duration():
    plan = video_service.resolve_plan(method="fps", info=info(), fps=2)
    assert plan.count == 20
    assert plan.interval == pytest.approx(0.5)
    assert plan.truncated is False


def test_count_plan_spreads_frames_evenly():
    plan = video_service.resolve_plan(method="count", info=info(duration=20), count=10)
    assert plan.count == 10
    assert plan.interval == pytest.approx(2.0)
    assert plan.fps == pytest.approx(0.5)


def test_interval_plan_uses_seconds_between_frames():
    plan = video_service.resolve_plan(method="interval", info=info(), interval=5)
    assert plan.count == 2
    assert plan.fps == pytest.approx(0.2)


def test_every_frame_plan_is_capped_and_flagged():
    plan = video_service.resolve_plan(method="every_frame", info=info(duration=60))
    assert plan.count == settings.max_output_images
    assert plan.truncated is True
    assert plan.fps is None


def test_count_above_limit_is_clamped():
    plan = video_service.resolve_plan(
        method="count", info=info(), count=settings.max_output_images + 50
    )
    assert plan.count == settings.max_output_images
    assert plan.truncated is True


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("fps", {"fps": 0}),
        ("fps", {"fps": 500}),
        ("interval", {"interval": 0}),
        ("count", {"count": 0}),
        ("nonsense", {}),
    ],
)
def test_invalid_requests_are_rejected(method, kwargs):
    with pytest.raises(video_service.VideoError):
        video_service.resolve_plan(method=method, info=info(), **kwargs)


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("interval", {"interval": 30}),   # longer than the video
        ("interval", {"interval": 10}),   # exactly the video length
        ("count", {"count": 1}),
        ("fps", {"fps": 0.05}),
    ],
)
def test_a_single_frame_request_takes_the_first_frame(method, kwargs):
    """A very low fps filter can emit nothing, so ask for frame one instead."""
    plan = video_service.resolve_plan(method=method, info=info(duration=10), **kwargs)
    assert plan.count == 1
    assert plan.fps is None, "a one-frame plan must not rely on the fps filter"


def test_multi_frame_plans_still_use_the_fps_filter():
    plan = video_service.resolve_plan(method="interval", info=info(duration=10), interval=4)
    assert plan.count == 3
    assert plan.fps == pytest.approx(0.25)


def test_parse_fps_handles_rationals_and_junk():
    assert video_service._parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert video_service._parse_fps("0/0") == 0.0
    assert video_service._parse_fps(None) == 0.0
