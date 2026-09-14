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
from src.domain.models import JobStatus
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
        max_attempts: int = 3,
    ) -> None:
        self.queue = queue
        self.job_store = job_store
        self.artifact_store = artifact_store
        self.providers = providers
        self.concurrency = max(1, concurrency)
        self.max_attempts = max(1, max_attempts)
        self._tasks: list[asyncio.Task] = []

    async def recover_orphaned_jobs(self) -> None:
        """Re-enqueue jobs left PENDING or GENERATING by a previous process
        that exited - crash, restart, redeploy - before a worker finished
        them. The queue that normally hands a job to a worker lives only in
        this process's memory, so a job that was merely mid-flight when the
        old process died has no other record that it still needs work; left
        alone it would sit at its last-persisted status forever. Call this
        once, before worker.start(), from create_app()'s before_serving hook.

        A job that has already used up self.max_attempts real attempts
        (Job.attempt_count, incremented once per attempt in _process below)
        is not retried again - it's marked FAILED instead, so a job that
        reliably crashes the process on every attempt fails loudly after a
        bounded number of tries rather than looping forever across restarts.
        """
        jobs = await self.job_store.list_all()
        for job in jobs:
            if job.status not in (JobStatus.PENDING, JobStatus.GENERATING):
                continue
            if job.attempt_count >= self.max_attempts:
                job.mark_failed(
                    f"Gave up after {job.attempt_count} attempt(s) - the "
                    "process exited before this job finished each time.",
                    code="max_retries_exceeded",
                )
                await self.job_store.save(job)
                logger.warning(
                    "Job %s exceeded max_attempts (%d); marking failed "
                    "instead of retrying again.",
                    job.id, self.max_attempts,
                )
                continue
            logger.info(
                "Recovering orphaned job %s (status=%s, attempt %d/%d) left "
                "over from a previous run.",
                job.id, job.status.value, job.attempt_count, self.max_attempts,
            )
            await self.queue.put(job.id)

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

        # Persisted before generate() runs (not after) so it's durable even
        # if this attempt crashes the process outright - recover_orphaned_
        # jobs above needs an accurate count of attempts already spent,
        # including ones that never got the chance to reach mark_failed.
        job.attempt_count += 1
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
