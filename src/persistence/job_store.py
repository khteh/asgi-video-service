"""Filesystem-backed job state persistence.

Each job lives at ``<output_dir>/jobs/<job_id>/status.json``. This module is
the *only* place that knows jobs are stored as JSON files on disk -
everything else (job service, API layer, worker) talks to the
``JobRepository`` protocol, so swapping in a real database later means
writing one new class here and nowhere else.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from src.domain.errors import JobNotFoundError
from src.domain.models import Job


class JobRepository(Protocol):
    async def save(self, job: Job) -> None: ...

    async def get(self, job_id: str) -> Job: ...

    async def list_all(self) -> list[Job]: ...

    def job_dir(self, job_id: str) -> Path: ...


class FileSystemJobStore:
    """Simple, dependency-free job persistence: one JSON file per job.

    Writes are staged to a temp file and atomically renamed so a reader
    never observes a half-written status.json, and a single asyncio.Lock
    serializes writes (fine at this service's scale; a real database would
    remove the need for it entirely).
    """

    def __init__(self, output_dir: Path) -> None:
        self.jobs_dir = Path(output_dir) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _status_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "status.json"

    async def save(self, job: Job) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, job)

    def _save_sync(self, job: Job) -> None:
        job_dir = self.job_dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = self._status_path(job.id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(job.to_dict(), indent=2))
        tmp_path.replace(path)

    async def get(self, job_id: str) -> Job:
        return await asyncio.to_thread(self._get_sync, job_id)

    def _get_sync(self, job_id: str) -> Job:
        path = self._status_path(job_id)
        if not path.exists():
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        data = json.loads(path.read_text())
        return Job.from_dict(data)

    async def list_all(self) -> list[Job]:
        return await asyncio.to_thread(self._list_all_sync)

    def _list_all_sync(self) -> list[Job]:
        jobs: list[Job] = []
        for status_path in self.jobs_dir.glob("*/status.json"):
            try:
                jobs.append(Job.from_dict(json.loads(status_path.read_text())))
            except (json.JSONDecodeError, OSError, KeyError):
                # A partially-written or corrupted file shouldn't take the
                # whole listing endpoint down.
                continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs
