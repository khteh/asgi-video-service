"""Background worker(s) that actually run generation for queued jobs.

Kept separate from JobService so the request/response path (submit/get/
list) never blocks on video generation: submitting a job just validates,
persists, and hands its id to an asyncio.Queue; one or more worker tasks
pull ids off that queue and do the slow work of calling a generation
provider and updating job state as it progresses.
"""
from __future__ import annotations

import asyncio
import logging

from src.domain.errors import ServiceError
from src.generation.registry import ProviderRegistry
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import JobRepository

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        *,
        queue: "asyncio.Queue[str]",
        job_store: JobRepository,
        artifact_store: ArtifactStore,
        providers: ProviderRegistry,
        concurrency: int = 2,
    ) -> None:
        self.queue = queue
        self.job_store = job_store
        self.artifact_store = artifact_store
        self.providers = providers
        self.concurrency = max(1, concurrency)
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks:
            return
        for i in range(self.concurrency):
            self._tasks.append(asyncio.create_task(self._run(i), name=f"job-worker-{i}"))
        logger.info("Started %d job worker(s).", self.concurrency)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _run(self, worker_index: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._process(job_id)
            except Exception:  # noqa: BLE001 - a worker loop must never die
                logger.exception(
                    "Worker %d: unhandled error processing job %s",
                    worker_index, job_id,
                )
            finally:
                self.queue.task_done()

    async def _process(self, job_id: str) -> None:
        job = await self.job_store.get(job_id)
        provider = self.providers.get(job.provider)

        async def on_progress(stage: str, progress: float) -> None:
            job.mark_generating(stage, progress)
            await self.job_store.save(job)

        job.mark_generating("starting", 0.0)
        await self.job_store.save(job)

        output_path = self.artifact_store.video_path(job.id)
        try:
            result = await provider.generate(
                job_id=job.id,
                query=job.query,
                difficulty=job.difficulty,
                output_path=output_path,
                on_progress=on_progress,
            )
        except ServiceError as exc:
            job.mark_failed(exc.message, code=exc.code)
            await self.job_store.save(job)
            logger.warning("Job %s failed: %s", job.id, exc.message)
            return
        except Exception as exc:  # noqa: BLE001 - normalize unexpected errors
            job.mark_failed(f"Unexpected error during generation: {exc}")
            await self.job_store.save(job)
            logger.exception("Job %s failed unexpectedly", job.id)
            return

        job.mark_completed(result)
        await self.job_store.save(job)
        logger.info("Job %s completed in %.1fs", job.id, result.duration_seconds)
