"""Timeout behavior of the ffmpeg/ffprobe subprocess wrappers in
video_builder.py.

Unlike test_video_builder.py's integration tests, these don't need a real
ffmpeg/ffprobe on PATH and aren't skipped when one is absent: subprocess
creation is mocked so the "process" hangs past its timeout deterministically
(a real asyncio.sleep, actually cancelled by asyncio.wait_for - not just a
mocked-away return value), rather than depending on real process/OS timing
or on installing ffmpeg in whatever environment runs this suite.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.errors import GenerationFailedError
from src.generation.simulated.video_builder import _kill, _run_ffmpeg, probe_duration


def _hanging_process() -> MagicMock:
    """A fake asyncio subprocess whose communicate() never returns on its
    own - it must actually be cancelled via the timeout, exactly like a
    real wedged ffmpeg/ffprobe process would have to be."""
    proc = MagicMock()
    proc.communicate = lambda: asyncio.sleep(3600)
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 0
    return proc


@pytest.mark.asyncio
async def test_run_ffmpeg_times_out_and_kills_the_process():
    proc = _hanging_process()
    with patch(
        "src.generation.simulated.video_builder.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        with pytest.raises(GenerationFailedError, match="timed out"):
            await _run_ffmpeg(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], timeout=0.01)
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_duration_times_out_and_kills_the_process(tmp_path):
    proc = _hanging_process()
    with patch(
        "src.generation.simulated.video_builder.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        with pytest.raises(GenerationFailedError, match="timed out"):
            await probe_duration(tmp_path / "audio.mp3", timeout=0.01)
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_ffmpeg_does_not_time_out_when_the_process_finishes_in_time():
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.kill = MagicMock()
    proc.returncode = 0
    with patch(
        "src.generation.simulated.video_builder.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        await _run_ffmpeg(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], timeout=5.0)
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_kill_tolerates_a_process_that_already_exited():
    """The gap between a timeout firing and _kill actually running is a
    real race - the process may have exited on its own in between, and
    that ProcessLookupError is expected, not something to surface."""
    proc = MagicMock()
    proc.kill = MagicMock(side_effect=ProcessLookupError)
    proc.wait = AsyncMock(return_value=None)

    await _kill(proc)  # must not raise

    proc.wait.assert_not_awaited()
