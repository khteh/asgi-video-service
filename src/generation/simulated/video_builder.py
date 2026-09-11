"""Assembles rendered slide images + narration audio into the final MP4.

Encodes at the configured resolution/fps (4K/60fps by default) and enforces
the hard duration cap by speeding up (never cropping mid-sentence, where
avoidable) if the authored narration runs long. Prefers an NVIDIA NVENC
hardware encoder when one is actually usable, and transparently falls back
to a software encoder (libx264) otherwise - a build with NVENC compiled in
but no GPU attached (common in dev containers/CI) must not crash the whole
pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.domain.errors import GenerationFailedError

logger = logging.getLogger(__name__)


@dataclass
class SlideClip:
    image_path: Path
    audio_path: Path
    min_duration: float = 1.75


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GenerationFailedError(
            f"ffprobe failed for {path}: {stderr.decode(errors='ignore')[-500:]}"
        )
    data = json.loads(stdout.decode())
    return float(data["format"]["duration"])


def detect_nvenc(nvenc_mode: str) -> bool:
    """Probe whether ffmpeg can *actually* encode with h264_nvenc right
    now - not just whether the encoder is compiled in. A build with NVENC
    support but no NVIDIA GPU/driver attached must fall back cleanly.
    """
    if nvenc_mode == "off":
        return False
    if nvenc_mode not in ("auto", "on"):
        logger.warning("Unknown NVENC_MODE %r, treating as 'auto'.", nvenc_mode)

    try:
        encoders = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    if "h264_nvenc" not in encoders:
        return False
    if nvenc_mode == "on":
        # Forced on: trust the caller, skip the (slower) capability probe.
        return True

    try:
        probe = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=15,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _atempo_chain(factor: float) -> Optional[str]:
    """ffmpeg's atempo filter only accepts 0.5-2.0 per instance; chain
    multiple instances for factors outside that range (defensive - our
    caller clamps well inside it in practice)."""
    if abs(factor - 1.0) < 1e-3:
        return None
    remaining = factor
    filters: list[str] = []
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def _encoder_quality_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    return ["-preset", "medium", "-crf", "18"]


async def _run_ffmpeg(cmd: list[str], cwd: Optional[Path] = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GenerationFailedError(
            f"ffmpeg command failed: {' '.join(cmd)}\n"
            f"{stderr.decode(errors='ignore')[-2000:]}"
        )


async def assemble_video(
    slides: list[SlideClip],
    output_path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    max_seconds: float,
    nvenc_mode: str = "auto",
) -> tuple[float, str]:
    """Builds the final MP4 at `output_path`.

    Returns (final_duration_seconds, hardware_acceleration_label).
    """
    if not slides:
        raise GenerationFailedError("Cannot assemble a video with zero slides.")

    # A small safety margin below the nominal cap absorbs frame-boundary
    # rounding in the ffmpeg encode (each clip's -t snaps to the nearest
    # frame), so the final artifact reliably comes in at or under
    # max_seconds instead of a few milliseconds over it.
    safety_margin = 0.5
    effective_cap = max(1.0, max_seconds - safety_margin)

    raw_durations = [
        max(s.min_duration, await probe_duration(s.audio_path)) for s in slides
    ]
    raw_total = sum(raw_durations)

    # Stage 1: speed up narration audio (natural-sounding up to ~1.5x) to
    # try to fit the cap.
    speed_factor = min(raw_total / effective_cap, 1.5) if raw_total > effective_cap else 1.0
    durations = [d / speed_factor for d in raw_durations]
    total = sum(durations)

    # Stage 2: if 1.5x speed-up still isn't enough (very verbose content),
    # proportionally compress every slide's on-screen time so the video
    # NEVER exceeds the hard cap - this is a requirement, not a preference.
    # Applied uniformly (not just to one slide) so no single slide is
    # destroyed; at this point the tail of a slide's sped-up narration may
    # get truncated, which only happens in this extreme edge case.
    if total > effective_cap:
        trim_scale = effective_cap / total
        durations = [d * trim_scale for d in durations]
        total = sum(durations)

    use_nvenc = detect_nvenc(nvenc_mode)
    encoder = "h264_nvenc" if use_nvenc else "libx264"
    atempo = _atempo_chain(speed_factor)

    workdir = output_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )

    clip_paths: list[Path] = []
    try:
        for i, (slide, duration) in enumerate(zip(slides, durations)):
            clip_path = workdir / f"_clip_{i:02d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-i", str(slide.image_path),
                "-i", str(slide.audio_path),
                "-t", f"{duration:.3f}",
                "-vf", video_filter,
                "-af", atempo or "anull",
                "-c:v", encoder,
                *_encoder_quality_args(encoder),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-pix_fmt", "yuv420p",
                "-shortest",
                str(clip_path),
            ]
            await _run_ffmpeg(cmd)
            clip_paths.append(clip_path)

        concat_list = workdir / "_concat.txt"
        concat_list.write_text("\n".join(f"file '{p.name}'" for p in clip_paths))
        final_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
        ]
        await _run_ffmpeg(final_cmd, cwd=workdir)
        final_duration = await probe_duration(output_path)
        if final_duration > max_seconds + 0.25:
            logger.warning(
                "Assembled video duration %.2fs exceeds the %.0fs cap after "
                "compression - this shouldn't happen; check slide count/"
                "narration length.",
                final_duration, max_seconds,
            )
    finally:
        for p in clip_paths:
            p.unlink(missing_ok=True)
        (workdir / "_concat.txt").unlink(missing_ok=True)

    return final_duration, ("nvenc" if use_nvenc else "cpu")
