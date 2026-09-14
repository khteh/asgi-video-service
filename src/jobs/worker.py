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
from src.domain.models import Job, JobStatus
from src.generation.registry import ProviderRegistry
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import JobRepository

logger = logging.getLogger(__name__)

# Shown to the user for any terminal (attempts-exhausted) job failure,
# regardless of what actually went wrong internally (a provider error, an
# unexpected exception, or the process itself dying mid-attempt) - the UI
# must never disclose internals of how the service works.
_GENERIC_FAILURE_MESSAGE = (
    "This video could not be generated after multiple attempts. "
    "Please try submitting your question again."
)


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
                job.mark_failed(_GENERIC_FAILURE_MESSAGE, code="max_retries_exceeded")
                await self.job_store.save(job)
                logger.warning(
                    "Job %s exceeded max_attempts (%d) across process "
                    "restarts; marking failed instead of retrying again.",
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
            await self._handle_attempt_failure(job, exc.message, exc_info=False)
            return
        except Exception as exc:  # noqa: BLE001 - normalize unexpected errors
            await self._handle_attempt_failure(
                job, f"Unexpected error during generation: {exc}", exc_info=True,
            )
            return

        job.mark_completed(result)
        await self.job_store.save(job)
        logger.info("Job %s completed in %.1fs", job.id, result.duration_seconds)

    async def _handle_attempt_failure(
        self, job: Job, message: str, *, exc_info: bool = False,
    ) -> None:
        """Handle a single failed attempt at generating ``job``.

        If attempts remain under self.max_attempts, this is NOT a terminal
        failure: the job is put back on the queue for another try, and its
        externally-visible state is left at GENERATING (stage "retrying") so
        nothing about the interim failure is ever exposed via the API or UI -
        submitting new jobs stays blocked (the job is still "in progress")
        and no partial error detail leaks out. Only once attempt_count has
        reached max_attempts is the job actually marked FAILED, at which
        point the message shown to the user is a generic, non-leaky one
        regardless of what internally went wrong.
        """
        log = logger.exception if exc_info else logger.warning
        if job.attempt_count < self.max_attempts:
            log(
                "Job %s attempt %d/%d failed (%s); retrying.",
                job.id, job.attempt_count, self.max_attempts, message,
            )
            job.mark_generating("retrying", 0.0)
            await self.job_store.save(job)
            await self.queue.put(job.id)
            return

        log(
            "Job %s exhausted all %d attempt(s); giving up. Last error: %s",
            job.id, self.max_attempts, message,
        )
        job.mark_failed(_GENERIC_FAILURE_MESSAGE, code="max_retries_exceeded")
        await self.job_store.save(job)
