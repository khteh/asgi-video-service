"""Covers JobWorker's orphan-job recovery and attempt-count bookkeeping.

This is the machinery that stops a job left PENDING/GENERATING by a
previous process (crash, restart, redeploy) from sitting at that status
forever - and, symmetrically, stops a job that fails every single attempt
from being retried forever across restarts instead of eventually being
given up on.

_process is called directly (bypassing the infinite _run loop / queue.get)
so these are plain, non-background-task unit tests - no worker.start()/
stop() or task-cancellation machinery needed.
"""
from __future__ import annotations

import asyncio

import pytest

from conftest import FakeProvider
from src.domain.errors import GenerationFailedError
from src.domain.models import (
    DifficultyLevel,
    GenerationProviderName,
    Job,
    JobStatus,
    new_job_id,
)
from src.generation.registry import ProviderRegistry
from src.jobs.worker import JobWorker
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import FileSystemJobStore


class _FailingProvider:
    """Always raises GenerationFailedError - a normal, provider-reported
    failure (as opposed to _CrashingProvider's unhandled exception)."""

    name = "failing"

    async def preflight(self) -> None:
        return None

    async def generate(self, *, job_id, query, difficulty, output_path, on_progress):
        raise GenerationFailedError("synthetic failure", code="test_failure")


class _CrashingProvider:
    """Raises a plain, un-normalized exception - exercises _process's
    generic `except Exception` branch, distinct from the ServiceError one
    _FailingProvider exercises."""

    name = "crashing"

    async def preflight(self) -> None:
        return None

    async def generate(self, *, job_id, query, difficulty, output_path, on_progress):
        raise RuntimeError("unexpected crash")


def _make_worker(tmp_path, *, provider=None, max_attempts: int = 3):
    job_store = FileSystemJobStore(tmp_path / "output")
    artifact_store = ArtifactStore(tmp_path / "output")
    providers = ProviderRegistry(
        {GenerationProviderName.SIMULATED: provider or FakeProvider(available=True)}
    )
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    worker = JobWorker(
        queue=queue,
        job_store=job_store,
        artifact_store=artifact_store,
        providers=providers,
        concurrency=1,
        max_attempts=max_attempts,
    )
    return worker, job_store, queue


def _make_job(*, status: JobStatus, attempt_count: int = 0) -> Job:
    job = Job(
        id=new_job_id(),
        query="How does the pH scale work?",
        difficulty=DifficultyLevel.BEGINNER,
        provider=GenerationProviderName.SIMULATED,
    )
    job.status = status
    job.attempt_count = attempt_count
    return job


@pytest.mark.asyncio
async def test_recover_reenqueues_pending_and_generating_jobs_under_the_cap(tmp_path):
    worker, job_store, queue = _make_worker(tmp_path, max_attempts=3)
    pending = _make_job(status=JobStatus.PENDING, attempt_count=0)
    generating = _make_job(status=JobStatus.GENERATING, attempt_count=1)
    await job_store.save(pending)
    await job_store.save(generating)

    await worker.recover_orphaned_jobs()

    enqueued = {queue.get_nowait() for _ in range(queue.qsize())}
    assert enqueued == {pending.id, generating.id}
    # Recovery only enqueues - it doesn't itself touch status/attempt_count;
    # that happens once a worker actually claims the job back in _process.
    assert (await job_store.get(pending.id)).status == JobStatus.PENDING
    assert (await job_store.get(generating.id)).status == JobStatus.GENERATING


@pytest.mark.asyncio
async def test_recover_ignores_completed_and_failed_jobs(tmp_path):
    worker, job_store, queue = _make_worker(tmp_path)
    completed = _make_job(status=JobStatus.COMPLETED)
    failed = _make_job(status=JobStatus.FAILED)
    await job_store.save(completed)
    await job_store.save(failed)

    await worker.recover_orphaned_jobs()

    assert queue.qsize() == 0
    assert (await job_store.get(completed.id)).status == JobStatus.COMPLETED
    assert (await job_store.get(failed.id)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_recover_gives_up_after_max_attempts_instead_of_looping_forever(tmp_path):
    worker, job_store, queue = _make_worker(tmp_path, max_attempts=3)
    exhausted = _make_job(status=JobStatus.GENERATING, attempt_count=3)
    await job_store.save(exhausted)

    await worker.recover_orphaned_jobs()

    assert queue.qsize() == 0
    stored = await job_store.get(exhausted.id)
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "max_retries_exceeded"


@pytest.mark.asyncio
async def test_recover_does_not_reenqueue_an_already_exhausted_job_twice(tmp_path):
    # A second recovery pass (e.g. a future change calling it more than
    # once) must not re-touch a job it already gave up on: FAILED isn't
    # PENDING/GENERATING any more, so recover_orphaned_jobs's own status
    # filter is what keeps this a no-op.
    worker, job_store, queue = _make_worker(tmp_path, max_attempts=1)
    job = _make_job(status=JobStatus.GENERATING, attempt_count=1)
    await job_store.save(job)

    await worker.recover_orphaned_jobs()
    await worker.recover_orphaned_jobs()

    assert queue.qsize() == 0
    assert (await job_store.get(job.id)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_process_increments_attempt_count_on_success(tmp_path):
    worker, job_store, _queue = _make_worker(tmp_path)
    job = _make_job(status=JobStatus.PENDING, attempt_count=0)
    await job_store.save(job)

    await worker._process(job.id)

    stored = await job_store.get(job.id)
    assert stored.attempt_count == 1
    assert stored.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_process_increments_attempt_count_on_service_error(tmp_path):
    worker, job_store, _queue = _make_worker(tmp_path, provider=_FailingProvider())
    job = _make_job(status=JobStatus.PENDING, attempt_count=1)
    await job_store.save(job)

    await worker._process(job.id)

    stored = await job_store.get(job.id)
    assert stored.attempt_count == 2
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "test_failure"


@pytest.mark.asyncio
async def test_process_increments_attempt_count_on_unexpected_exception(tmp_path):
    worker, job_store, _queue = _make_worker(tmp_path, provider=_CrashingProvider())
    job = _make_job(status=JobStatus.PENDING, attempt_count=0)
    await job_store.save(job)

    await worker._process(job.id)

    stored = await job_store.get(job.id)
    assert stored.attempt_count == 1
    assert stored.status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_full_recovery_then_processing_cycle_completes_an_orphaned_job(tmp_path):
    """End-to-end: a job left PENDING as if a previous process had crashed
    right after JobService.submit persisted it but before any worker
    claimed it. Recovery re-enqueues it, and the normal worker path then
    finishes it - this is the actual scenario the original bug report
    (a job stuck at "pending" forever) needed to stop happening."""
    worker, job_store, queue = _make_worker(tmp_path, max_attempts=3)
    orphaned = _make_job(status=JobStatus.PENDING, attempt_count=0)
    await job_store.save(orphaned)

    await worker.recover_orphaned_jobs()
    assert queue.qsize() == 1

    job_id = await queue.get()
    await worker._process(job_id)
    queue.task_done()

    stored = await job_store.get(orphaned.id)
    assert stored.status == JobStatus.COMPLETED
    assert stored.attempt_count == 1
