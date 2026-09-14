"""Timeout behavior of tts.synthesize (src/generation/simulated/tts.py).

Mocks edge_tts.Communicate.save so this doesn't need real network access to
the edge-tts speech endpoint - it makes the "call" hang past its timeout
deterministically (a real, cancellable asyncio.sleep, actually cancelled by
asyncio.wait_for - not just a mocked-away return value), the same way
test_video_builder_timeouts.py exercises the ffmpeg/ffprobe wrappers.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.domain.errors import GenerationFailedError
from src.generation.simulated import tts


@pytest.mark.asyncio
async def test_synthesize_times_out_instead_of_hanging_forever(tmp_path):
    output_path = tmp_path / "narration.mp3"
    with patch(
        "src.generation.simulated.tts.edge_tts.Communicate.save",
        lambda self, *_args, **_kwargs: asyncio.sleep(3600),
    ):
        with pytest.raises(GenerationFailedError, match="timed out"):
            await tts.synthesize("hello", "en-US-AriaNeural", output_path, timeout=0.01)


@pytest.mark.asyncio
async def test_synthesize_raises_on_empty_audio_file(tmp_path):
    output_path = tmp_path / "narration.mp3"

    async def _save_nothing(self, *_args, **_kwargs) -> None:
        # edge-tts "succeeded" but produced no audio - synthesize() must
        # still fail rather than silently shipping a job with missing
        # narration. (Pre-existing behavior; re-checked here alongside the
        # new timeout path since both now share the same try/except.)
        output_path.touch()

    with patch("src.generation.simulated.tts.edge_tts.Communicate.save", _save_nothing):
        with pytest.raises(GenerationFailedError, match="empty audio"):
            await tts.synthesize("hello", "en-US-AriaNeural", output_path, timeout=5.0)
