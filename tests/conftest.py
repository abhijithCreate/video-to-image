from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Templates and static files are resolved relative to the working directory.
os.chdir(ROOT)


@pytest.fixture(autouse=True)
def temp_jobs_dir(tmp_path, monkeypatch):
    """Point every test at an isolated job root."""
    from app.config import settings

    monkeypatch.setattr(settings, "temp_dir", tmp_path / "jobs")
    return settings.temp_dir


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_frame():
    """A frame as it arrives from FFmpeg: raw RGBA bytes wrapped by Pillow."""
    from app.services import image as image_service

    size = (640, 360)
    data = bytes(bytearray([20, 120, 240, 255]) * (size[0] * size[1]))
    return image_service.open_frame(data, size)
