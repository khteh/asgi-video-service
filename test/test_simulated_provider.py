"""Covers SimulatedVideoProvider.preflight()'s ffmpeg/ffprobe binary check.

Uses monkeypatch to simulate both "present" and "missing" PATH states
rather than depending on the actual sandbox having (or not having) ffmpeg
installed - test_video_builder.py already covers the real-binary
integration path and skips itself when ffmpeg truly isn't available.
"""
from __future__ import annotations

import pytest

from src.domain.errors import ProviderUnavailableError
from src.generation.simulated.provider import SimulatedVideoProvider


@pytest.mark.asyncio
async def test_preflight_passes_when_ffmpeg_and_ffprobe_are_on_path(settings, monkeypatch):
    monkeypatch.setattr(
        "src.generation.simulated.provider.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    provider = SimulatedVideoProvider(settings)

    await provider.preflight()  # must not raise


@pytest.mark.asyncio
async def test_preflight_fails_when_ffmpeg_is_missing(settings, monkeypatch):
    monkeypatch.setattr(
        "src.generation.simulated.provider.shutil.which",
        lambda binary: None if binary == "ffmpeg" else f"/usr/bin/{binary}",
    )
    provider = SimulatedVideoProvider(settings)

    with pytest.raises(ProviderUnavailableError, match="ffmpeg"):
        await provider.preflight()


@pytest.mark.asyncio
async def test_preflight_fails_when_both_binaries_are_missing(settings, monkeypatch):
    monkeypatch.setattr(
        "src.generation.simulated.provider.shutil.which", lambda _binary: None
    )
    provider = SimulatedVideoProvider(settings)

    with pytest.raises(ProviderUnavailableError, match="missing required binaries") as exc_info:
        await provider.preflight()
    assert exc_info.value.code == "missing_binary"
