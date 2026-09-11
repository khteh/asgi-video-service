"""Generic/vendor-agnostic real AI video-generation provider.

This is the "real provider" half of the plug-and-play boundary
(requirement 4). It is deliberately vendor-agnostic: the request/response
shape below is a reasonable generic contract (submit a generation job,
poll it, download the artifact) that you point at a real text-to-video +
TTS API by setting AI_VIDEO_BASE_URL / AI_VIDEO_API_KEY and, if the real
API's request/response shape differs, adjusting `_submit`/`_poll`/
`_download` below - nothing outside this file needs to change.

Two things this class is responsible for that the simulated provider is
not:

1. preflight() must fail fast, BEFORE a job is created, on a missing API
   key or an unreachable/erroring upstream (failure case 3 in the spec).
2. generate() should ideally ask the upstream provider to itself respect
   the 90s/4K/60fps constraints (passed in the request payload below); if
   a real provider doesn't support one of those knobs natively, the
   simulated provider's ffmpeg post-processing step could be reused here
   to normalize its output - left as a clearly-marked extension point.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from src.config import Settings
from src.domain.errors import GenerationFailedError, ProviderUnavailableError
from src.domain.models import DifficultyLevel, GenerationResult

from ..base import ProgressCallback

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
_POLL_TIMEOUT_SECONDS = 20 * 60.0


class GenericAIVideoProvider:
    name = "ai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.ai_video_api_key}",
            "Content-Type": "application/json",
        }

    async def preflight(self) -> None:
        """Fail fast, with no side effects, when this provider can't
        currently service a request - so the job service never creates a
        job for a request that's doomed from the start.
        """
        api_key = self.settings.ai_video_api_key
        if not api_key or not api_key.strip():
            raise ProviderUnavailableError(
                "AI video generation is not configured: no API key set "
                "(AI_VIDEO_API_KEY).",
                code="missing_api_key",
            )

        url = self.settings.ai_video_base_url.rstrip("/") + self.settings.ai_video_preflight_path
        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_video_timeout_seconds) as client:
                resp = await client.get(url, headers=self._headers())
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"Could not reach the AI video provider at {url!r}: {exc}. "
                "Check network connectivity.",
                code="no_network",
            ) from exc

        if resp.status_code in (401, 403):
            raise ProviderUnavailableError(
                "The AI video provider rejected the configured API key "
                f"(HTTP {resp.status_code}).",
                code="invalid_api_key",
            )
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"The AI video provider is currently unavailable (HTTP {resp.status_code}).",
                code="upstream_unavailable",
            )
        # Any other 2xx/4xx is treated as "reachable" - a stub health
        # endpoint returning 404 is fine, that's still a live host.

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
        payload = {
            "job_id": job_id,
            "prompt": query,
            "difficulty": difficulty.value,
            "slide_count": difficulty.slide_count,
            "max_duration_seconds": settings.max_video_seconds,
            "width": settings.video_width,
            "height": settings.video_height,
            "fps": settings.video_fps,
            "voice_style": "natural",
        }

        async with httpx.AsyncClient(timeout=settings.ai_video_timeout_seconds) as client:
            await on_progress("submitting_to_ai_provider", 0.1)
            generation_id = await self._submit(client, payload)

            await on_progress("waiting_for_ai_provider", 0.2)
            result_meta = await self._poll(client, generation_id, on_progress)

            await on_progress("downloading_artifact", 0.9)
            await self._download(client, result_meta["download_url"], output_path)

        return GenerationResult(
            video_path=str(output_path),
            duration_seconds=float(result_meta.get("duration_seconds", settings.max_video_seconds)),
            slide_count=int(result_meta.get("slide_count", difficulty.slide_count)),
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.video_fps,
            provider=self.name,
            narration_voice=result_meta.get("voice"),
            hardware_acceleration="provider_managed",
        )

    async def _submit(self, client: httpx.AsyncClient, payload: dict) -> str:
        url = self.settings.ai_video_base_url.rstrip("/") + "/generations"
        try:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GenerationFailedError(
                f"Failed to submit generation request to AI provider: {exc}"
            ) from exc
        data = resp.json()
        generation_id = data.get("id") or data.get("generation_id")
        if not generation_id:
            raise GenerationFailedError(
                "AI provider response did not include a generation id."
            )
        return generation_id

    async def _poll(
        self,
        client: httpx.AsyncClient,
        generation_id: str,
        on_progress: ProgressCallback,
    ) -> dict:
        url = self.settings.ai_video_base_url.rstrip("/") + f"/generations/{generation_id}"
        elapsed = 0.0
        while elapsed < _POLL_TIMEOUT_SECONDS:
            try:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise GenerationFailedError(
                    f"Failed to poll AI provider generation status: {exc}"
                ) from exc
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return data
            if status == "failed":
                raise GenerationFailedError(
                    f"AI provider reported generation failure: {data.get('error', 'unknown error')}"
                )
            progress = float(data.get("progress", 0.2))
            await on_progress("waiting_for_ai_provider", min(0.85, 0.2 + progress * 0.6))
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS
        raise GenerationFailedError("Timed out waiting for the AI provider to finish generation.")

    async def _download(self, client: httpx.AsyncClient, download_url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with client.stream("GET", download_url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as exc:
            raise GenerationFailedError(
                f"Failed to download generated video from AI provider: {exc}"
            ) from exc
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise GenerationFailedError("AI provider returned an empty video file.")
