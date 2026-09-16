"""Covers the core contract: bad input or an unready provider must return
an error immediately and create NO job at all.
"""
from __future__ import annotations

import asyncio

import pytest

from conftest import FakeProvider
from src.domain.errors import (
    InvalidQueryError,
    JobNotDeletableError,
    JobNotFoundError,
    NotStemRelevantError,
    ProviderUnavailableError,
)
from src.domain.models import GenerationProviderName, GenerationResult
from src.generation.registry import ProviderRegistry
from src.jobs.service import JobService
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import FileSystemJobStore
from src.validation.composite import CompositeQueryValidator
from src.validation.rule_based import RuleBasedValidator


@pytest.fixture
def job_service(tmp_path):
    job_store = FileSystemJobStore(tmp_path / "output")
    artifact_store = ArtifactStore(tmp_path / "output")
    validator = CompositeQueryValidator(rule_based=RuleBasedValidator(), llm=None)
    providers = ProviderRegistry(
        {
            GenerationProviderName.SIMULATED: FakeProvider(available=True),
            GenerationProviderName.AI: FakeProvider(available=False),
        }
    )
    queue: asyncio.Queue = asyncio.Queue()
    service = JobService(
        validator=validator,
        providers=providers,
        job_store=job_store,
        artifact_store=artifact_store,
        queue=queue,
    )
    return service, job_store


@pytest.mark.asyncio
async def test_valid_query_creates_a_pending_job(job_service):
    service, store = job_service
    job = await service.submit(
        "How does the pH scale work?", difficulty="beginner", provider="simulated"
    )
    assert job.status.value == "pending"
    assert job.difficulty.value == "beginner"
    stored = await store.get(job.id)
    assert stored.query == "How does the pH scale work?"
    assert await store.list_all() == [stored]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_query", ["", "   ", "~!@#$", "12345", "     "])
async def test_invalid_query_creates_no_job(job_service, bad_query):
    service, store = job_service
    with pytest.raises(InvalidQueryError):
        await service.submit(bad_query)
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_off_topic_query_creates_no_job(job_service):
    service, store = job_service
    with pytest.raises(NotStemRelevantError):
        await service.submit("What is your favorite pizza topping?")
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_unavailable_ai_provider_creates_no_job(job_service):
    service, store = job_service
    with pytest.raises(ProviderUnavailableError):
        await service.submit("How does the pH scale work?", provider="ai")
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_default_difficulty_and_provider_when_omitted(job_service):
    service, store = job_service
    job = await service.submit("How does the pH scale work?")
    assert job.difficulty.value == "intermediate"
    assert job.provider.value == "simulated"


@pytest.mark.asyncio
async def test_delete_completed_job_removes_status_and_artifacts(job_service):
    service, store = job_service
    job = await service.submit("How does the pH scale work?")

    video_path = service.artifact_store.video_path(job.id)
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake video")
    job_dir = service.artifact_store.job_dir(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "thumbnail.png").write_bytes(b"fake thumbnail")

    job.mark_completed(
        GenerationResult(
            video_path=str(video_path),
            duration_seconds=42.0,
            slide_count=6,
            width=1920,
            height=1080,
            fps=60,
            provider="simulated",
        )
    )
    await store.save(job)

    await service.delete_job(job.id)

    with pytest.raises(JobNotFoundError):
        await store.get(job.id)
    assert not video_path.exists()
    assert not job_dir.exists()


@pytest.mark.asyncio
async def test_delete_failed_job_succeeds(job_service):
    service, store = job_service
    job = await service.submit("How does the pH scale work?")
    job.mark_failed("boom")
    await store.save(job)

    await service.delete_job(job.id)

    with pytest.raises(JobNotFoundError):
        await store.get(job.id)


@pytest.mark.asyncio
async def test_delete_pending_job_raises_conflict_and_keeps_job(job_service):
    service, store = job_service
    job = await service.submit("How does the pH scale work?")

    with pytest.raises(JobNotDeletableError):
        await service.delete_job(job.id)

    assert await store.get(job.id) is not None


@pytest.mark.asyncio
async def test_delete_generating_job_raises_conflict(job_service):
    service, store = job_service
    job = await service.submit("How does the pH scale work?")
    job.mark_generating("rendering", 0.5)
    await store.save(job)

    with pytest.raises(JobNotDeletableError):
        await service.delete_job(job.id)

    assert await store.get(job.id) is not None


@pytest.mark.asyncio
async def test_delete_unknown_job_raises_not_found(job_service):
    service, _store = job_service
    with pytest.raises(JobNotFoundError):
        await service.delete_job("does-not-exist")
