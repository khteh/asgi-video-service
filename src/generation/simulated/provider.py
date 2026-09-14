"""Simulated video-generation provider.

Fully self-contained: no API key required, and it needs network access
only for edge-tts's free speech endpoint (narration audio). It produces a
*real* MP4 - actual rendered slide visuals plus narrated audio - not a
stand-in file, so the whole pipeline (slide-count scaling by difficulty,
the 90s/4K/60fps encode constraints, NVENC/CPU hardware fallback) is
exercised end-to-end without needing a paid AI video-generation provider.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.config import Settings
from src.domain.errors import GenerationFailedError
from src.domain.models import DifficultyLevel, GenerationResult

from ..base import ProgressCallback
from . import renderer, tts
from .slides import build_slides, fit_narration_to_budget
from .video_builder import SlideClip, assemble_video

logger = logging.getLogger(__name__)

# ~150 words/minute is a natural narration pace. Used only to pre-trim
# authored narration text to a sane length before synthesis - the timing
# that actually matters (the 90s hard cap) is enforced from *measured*
# audio duration in video_builder, not from this estimate.
_WORDS_PER_SECOND = 2.5


class SimulatedVideoProvider:
    name = "simulated"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def preflight(self) -> None:
        # Fully offline/keyless by design - nothing to check.
        return None

    async def generate(
        self,
        *,
        job_id: str,
        query: str,
        difficulty: DifficultyLevel,
        output_path: Path,
        on_progress: ProgressCallback,
    ) -> GenerationResult:
        settings = self.settings
        # output_path is now a flat file directly under output/videos/
        # (<job_id>.mp4), shared by every job in that directory - not a
        # per-job directory itself - so this provider's own scratch/derived
        # files (slides, audio, thumbnail) need their own per-job
        # subdirectory to avoid concurrent jobs colliding on filenames.
        # output_path.stem is the job id (see ArtifactStore.video_path), so
        # output_path.parent / output_path.stem reconstructs exactly the
        # per-job scratch directory ArtifactStore.job_dir()/thumbnail_path()
        # expect to find things in, without needing that store passed in
        # here too.
        job_dir = output_path.parent / output_path.stem
        slides_dir = job_dir / "slides"
        audio_dir = job_dir / "audio"
        slides_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

        await on_progress("generating_slide_content", 0.05)
        slides = build_slides(query, difficulty)
        if not slides:
            raise GenerationFailedError(
                "No slide content could be generated for this query."
            )

        # Reserve a little breathing room off the cap, then split what's
        # left evenly as a per-slide word budget so narration is authored
        # to roughly fit before we even measure real audio duration.
        usable_seconds = max(10.0, settings.max_video_seconds - 4.0)
        per_slide_seconds = usable_seconds / len(slides)
        max_words_per_slide = max(12, int(per_slide_seconds * _WORDS_PER_SECOND))

        clips: list[SlideClip] = []
        total_slides = len(slides)
        for slide in slides:
            progress = 0.1 + 0.5 * (slide.index / max(1, total_slides))
            await on_progress(
                f"rendering_slide_{slide.index + 1}_of_{total_slides}", progress
            )

            image_path = slides_dir / f"slide_{slide.index:02d}.png"
            renderer.render_slide(
                slide,
                total_slides=total_slides,
                difficulty_label=difficulty.value,
                topic=query,
                width=settings.video_width,
                height=settings.video_height,
                output_path=image_path,
            )

            narration_text = fit_narration_to_budget(slide.narration, max_words_per_slide)
            audio_path = audio_dir / f"slide_{slide.index:02d}.mp3"
            await tts.synthesize(
                narration_text, settings.tts_voice, audio_path,
                timeout=settings.tts_timeout_seconds,
            )

            clips.append(SlideClip(image_path=image_path, audio_path=audio_path))

        await on_progress("assembling_video", 0.7)
        duration, hardware = await assemble_video(
            clips,
            output_path,
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.video_fps,
            max_seconds=settings.max_video_seconds,
            nvenc_mode=settings.nvenc_mode,
            ffmpeg_timeout_seconds=settings.ffmpeg_timeout_seconds,
            ffprobe_timeout_seconds=settings.ffprobe_timeout_seconds,
        )

        await on_progress("finalizing", 0.95)
        thumbnail_path = job_dir / "thumbnail.png"
        try:
            shutil.copyfile(clips[0].image_path, thumbnail_path)
        except OSError:
            logger.warning("Could not write thumbnail for job %s", job_id)

        return GenerationResult(
            video_path=str(output_path),
            duration_seconds=duration,
            slide_count=total_slides,
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.video_fps,
            provider=self.name,
            narration_voice=settings.tts_voice,
            hardware_acceleration=hardware,
        )
