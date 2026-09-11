"""Integration tests for the ffmpeg assembly step. Skipped automatically if
ffmpeg isn't on PATH, but the service requires ffmpeg to function at all,
so this should run in any real deployment/dev environment.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image

from src.generation.simulated.video_builder import SlideClip, assemble_video, detect_nvenc

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_clip(tmp_path, index: int, duration: float, width=320, height=180) -> SlideClip:
    image_path = tmp_path / f"slide_{index}.png"
    Image.new("RGB", (width, height), (10, 20, 30)).save(image_path)
    audio_path = tmp_path / f"slide_{index}.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
            "-ar", "24000", "-ac", "1", str(audio_path),
        ],
        check=True,
    )
    return SlideClip(image_path=image_path, audio_path=audio_path)


@pytest.mark.asyncio
async def test_assemble_video_produces_a_playable_file_within_the_cap(tmp_path):
    clips = [_make_clip(tmp_path, i, duration=3) for i in range(4)]
    out = tmp_path / "video.mp4"
    duration, hardware = await assemble_video(
        clips, out, width=320, height=180, fps=24, max_seconds=90, nvenc_mode="auto"
    )
    assert out.exists() and out.stat().st_size > 0
    assert duration <= 90
    assert hardware in ("nvenc", "cpu")


@pytest.mark.asyncio
async def test_assemble_video_never_exceeds_the_hard_cap(tmp_path):
    # 9 slides x 20s of narration each (180s raw) is far beyond what real
    # authored content should ever produce, but the cap must hold anyway.
    clips = [_make_clip(tmp_path, i, duration=20) for i in range(9)]
    out = tmp_path / "video_long.mp4"
    duration, _ = await assemble_video(
        clips, out, width=320, height=180, fps=24, max_seconds=90, nvenc_mode="auto"
    )
    assert duration <= 90


def test_detect_nvenc_off_mode_is_always_false():
    assert detect_nvenc("off", 3840, 2160) is False
