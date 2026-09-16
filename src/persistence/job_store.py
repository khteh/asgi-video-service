"""Filesystem-backed job state persistence.

Each job lives at ``<output_dir>/jobs/<job_id>.json`` - a flat file named
by job id, deliberately not a per-job subdirectory: nothing else needs to
live alongside a job's status (the generated video/thumbnail/working files
live under ``<output_dir>/videos/`` instead, see ArtifactStore), so there's
no reason for "jobs" to be anything other than one JSON file per job. This
module is the *only* place that knows jobs are stored as JSON files on disk
- everything else (job service, API layer, worker) talks to the
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

    async def delete(self, job_id: str) -> None: ...


class FileSystemJobStore:
    """Simple, dependency-free job persistence: one JSON file per job.

    Writes are staged to a temp file and atomically renamed so a reader
    never observes a half-written status file, and a single asyncio.Lock
    serializes writes (fine at this service's scale; a real database would
    remove the need for it entirely).
    """

    def __init__(self, output_dir: Path) -> None:
        # Deliberately no mkdir here: constructing a store (which happens
        # every time create_app() runs, including as a side effect of just
        # importing src/main.py for its create_app symbol - see the trailing
        # `app = create_app()` at the bottom of that module) must not touch
        # disk. The directory is created lazily, on first actual write, in
        # _save_sync below. Reads (_get_sync's path.exists(), _list_all_sync's
        # glob) are both safe to call against a directory that doesn't exist
        # yet.
        self.jobs_dir = Path(output_dir) / "jobs"
        self._lock = asyncio.Lock()

    def _status_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    async def save(self, job: Job) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, job)

    def _save_sync(self, job: Job) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
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
        for status_path in self.jobs_dir.glob("*.json"):
            try:
                jobs.append(Job.from_dict(json.loads(status_path.read_text())))
            except (json.JSONDecodeError, OSError, KeyError):
                # A partially-written or corrupted file shouldn't take the
                # whole listing endpoint down.
                continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, job_id)

    def _delete_sync(self, job_id: str) -> None:
        path = self._status_path(job_id)
        if not path.exists():
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        path.unlink()
