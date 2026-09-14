"""Shared test fixtures and helpers.

FakeProvider stands in for both the simulated and AI providers in tests
that only need to exercise job-service/API wiring - it touches no ffmpeg,
no edge-tts, and no network, so those tests run fast and deterministically
in any environment (including CI without a GPU or internet access).
"""
from __future__ import annotations

import pytest

from src.config import Settings
from src.domain.errors import ProviderUnavailableError
from src.domain.models import GenerationResult

@pytest.fixture
def settings(tmp_path):
    return Settings(output_dir=tmp_path / "output")

class FakeProvider:
    name = "fake"

    def __init__(self, *, available: bool = True):
        self.available = available
        self.preflight_calls = 0
        self.generate_calls = 0

    async def preflight(self) -> None:
        self.preflight_calls += 1
        if not self.available:
            raise ProviderUnavailableError(
                "fake provider unavailable", code="fake_unavailable"
            )

    async def generate(self, *, job_id, query, difficulty, output_path, on_progress):
        self.generate_calls += 1
        await on_progress("starting", 0.1)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-mp4-bytes")
        await on_progress("finalizing", 0.9)
        return GenerationResult(
            video_path=str(output_path),
            duration_seconds=42.0,
            slide_count=difficulty.slide_count,
            width=3840,
            height=2160,
            fps=60,
            provider=self.name,
            narration_voice="fake-voice",
            hardware_acceleration="cpu",
        )

@pytest.fixture
def fake_provider():
    return FakeProvider(available=True)
