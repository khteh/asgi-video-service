"""Locates generated video artifacts on disk.

Kept deliberately separate from JobRepository (which only persists job
*state*) so the boundary between job state and artifacts required by the
spec is explicit in the code, not just conceptual: this is the only class
that knows the on-disk filename convention for a job's video/thumbnail.
"""
from __future__ import annotations

from pathlib import Path

from src.domain.errors import ArtifactNotFoundError
from src.domain.models import Job, JobStatus

VIDEO_FILENAME = "video.mp4"
THUMBNAIL_FILENAME = "thumbnail.png"


class ArtifactStore:
    def __init__(self, output_dir: Path) -> None:
        self.jobs_dir = Path(output_dir) / "jobs"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def video_path(self, job_id: str) -> Path:
        """Canonical path a generation provider should write the finished
        video to for a given job (whether or not it exists yet)."""
        return self.job_dir(job_id) / VIDEO_FILENAME

    def thumbnail_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / THUMBNAIL_FILENAME

    def resolve_video(self, job: Job) -> Path:
        """Return the on-disk path of a completed job's video, raising
        ArtifactNotFoundError if the job isn't complete or the file is
        missing (e.g. deleted out from under the service)."""
        if job.status != JobStatus.COMPLETED or job.result is None:
            raise ArtifactNotFoundError(
                f"Job '{job.id}' has no completed video artifact yet "
                f"(status: {job.status.value})."
            )
        path = Path(job.result.video_path)
        if not path.is_absolute():
            path = self.job_dir(job.id) / path.name
        if not path.exists():
            raise ArtifactNotFoundError(
                f"Video artifact for job '{job.id}' is missing on disk."
            )
        return path

    def resolve_thumbnail(self, job: Job) -> Path:
        """Return the on-disk path of a completed job's thumbnail image,
        raising ArtifactNotFoundError if it isn't available."""
        if job.status != JobStatus.COMPLETED:
            raise ArtifactNotFoundError(
                f"Job '{job.id}' has no thumbnail yet (status: {job.status.value})."
            )
        path = self.thumbnail_path(job.id)
        if not path.exists():
            raise ArtifactNotFoundError(f"Thumbnail for job '{job.id}' is missing on disk.")
        return path
