"""Job orchestration.

The single place that sequences validation, provider preflight,
persistence, and handing work to the async worker queue. It doesn't know
*how* a query is validated, *how* a provider generates a video, or *how* a
job is stored - it only sequences those steps, and it is what enforces the
spec's "no job is ever created for a doomed request" contract for all
three failure cases.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.domain.errors import JobNotDeletableError
from src.domain.models import (
    DifficultyLevel,
    GenerationProviderName,
    Job,
    JobStatus,
    new_job_id,
)
from src.generation.registry import ProviderRegistry
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import JobRepository
from src.validation.base import QueryValidator

logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        *,
        validator: QueryValidator,
        providers: ProviderRegistry,
        job_store: JobRepository,
        artifact_store: ArtifactStore,
        queue: "asyncio.Queue[str]",
        default_provider: GenerationProviderName = GenerationProviderName.SIMULATED,
    ) -> None:
        self.validator = validator
        self.providers = providers
        self.job_store = job_store
        self.artifact_store = artifact_store
        self.queue = queue
        self.default_provider = default_provider

    async def submit(
        self,
        raw_query: str,
        *,
        difficulty: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Job:
        """Validate the request and create+persist a PENDING job.

        Raises InvalidQueryError, NotStemRelevantError, or
        ProviderUnavailableError WITHOUT creating any job at all - this is
        the exact contract the spec's failure cases require: bad input or
        an unready provider must fail immediately, before any job exists.
        """
        provider_name = (
            GenerationProviderName.from_str(provider) if provider else self.default_provider
        )
        difficulty_level = DifficultyLevel.from_str(difficulty)

        # 1. Structural + semantic validation - never creates a job.
        query = await self.validator.validate(raw_query)

        # 2. Provider preflight - never creates a job either. This is what
        #    makes "no network / bad API key in ai mode" return an
        #    immediate error instead of a job that's doomed to fail later.
        provider_impl = self.providers.get(provider_name)
        await provider_impl.preflight()

        # 3. Only now does a Job start existing.
        job = Job(
            id=new_job_id(),
            query=query,
            difficulty=difficulty_level,
            provider=provider_name,
        )
        await self.job_store.save(job)
        await self.queue.put(job.id)
        logger.info(
            "Job %s created (provider=%s, difficulty=%s): %r",
            job.id, provider_name.value, difficulty_level.value, query,
        )
        return job

    async def get(self, job_id: str) -> Job:
        return await self.job_store.get(job_id)

    async def list_jobs(self) -> list[Job]:
        return await self.job_store.list_all()

    async def delete_job(self, job_id: str) -> None:
        """Deletes a job's status file and any artifacts it produced.

        Raises JobNotFoundError (via job_store.get) if the job doesn't
        exist, or JobNotDeletableError if it's still PENDING/GENERATING -
        the worker may be actively reading/writing that job's status and
        artifact files, and deleting them out from under it would race with
        that. Only a terminal-state job (COMPLETED or FAILED) is safe to
        delete.
        """
        job = await self.job_store.get(job_id)
        if job.status in (JobStatus.PENDING, JobStatus.GENERATING):
            raise JobNotDeletableError(
                f"Job '{job_id}' is still {job.status.value} and can't be "
                "deleted yet - wait for it to finish."
            )
        await self.artifact_store.delete_artifacts(job_id)
        await self.job_store.delete(job_id)
        logger.info("Job %s deleted", job_id)
