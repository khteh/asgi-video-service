"""Provider interface every video-generation backend must implement.

This Protocol is the plug-and-play boundary (requirement 4): the job
service and API layer only ever talk to VideoGenerationProvider, never to
SimulatedVideoProvider or the AI provider directly. Adding a new backend
means implementing `preflight` and `generate` and registering it in
registry.py - nothing else in the codebase needs to change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol

from src.domain.models import DifficultyLevel, GenerationResult

ProgressCallback = Callable[[str, float], Awaitable[None]]
"""async (stage_name, progress_0_to_1) -> None - a provider calls this
periodically while it works so the job service can persist live progress.
"""


class VideoGenerationProvider(Protocol):
    name: str

    async def preflight(self) -> None:
        """Cheap readiness check with no side effects, called BEFORE a job
        is created. Must raise ProviderUnavailableError if the provider
        can't currently service a request (missing/invalid API key, no
        network/upstream unreachable). This is what makes failure case 3
        ("no network or improper API key in ai mode") return an immediate
        error without ever creating a job. The simulated provider's
        preflight is a no-op.
        """
        ...

    async def generate(
        self,
        *,
        job_id: str,
        query: str,
        difficulty: DifficultyLevel,
        output_path: Path,
        on_progress: ProgressCallback,
    ) -> GenerationResult:
        """Produce a video for `query` at `output_path` and return a
        GenerationResult describing it.

        Raises GenerationFailedError on failure; the job service catches
        that and marks the job FAILED rather than letting it propagate as
        an HTTP error (the job already exists by this point).
        """
        ...
