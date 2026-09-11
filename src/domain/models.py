"""Core domain models.

These types are the boundary between the API layer, the job service, the
generation providers and the persistence layer. Nothing outside this module
should need to know how a job is stored on disk or how a video is produced.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    return uuid.uuid4().hex


class JobStatus(str, Enum):
    """Lifecycle of a video generation job.

    PENDING    -> job accepted and persisted, waiting for a worker
    GENERATING -> a provider is actively producing slides/audio/video
    COMPLETED  -> artifact is available
    FAILED     -> generation failed after the job was created (see error_message)

    Requests that fail validation or provider preflight NEVER produce a Job
    at all (see jobs/service.py) - only checks that pass reach PENDING.
    """

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class DifficultyLevel(str, Enum):
    """Learner difficulty level. Controls how many slides a video gets."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"

    @property
    def slide_count(self) -> int:
        """Number of slides scaled by difficulty (requirement: scale slide
        count by difficulty). Kept low enough that, even at the highest
        difficulty, each slide still gets a comfortable share of the
        90-second hard cap.
        """
        return _SLIDE_COUNTS[self]

    @classmethod
    def from_str(cls, value: Optional[str]) -> "DifficultyLevel":
        if not value:
            return cls.INTERMEDIATE
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.INTERMEDIATE


_SLIDE_COUNTS = {
    DifficultyLevel.BEGINNER: 4,
    DifficultyLevel.INTERMEDIATE: 6,
    DifficultyLevel.ADVANCED: 9,
}


class GenerationProviderName(str, Enum):
    SIMULATED = "simulated"
    AI = "ai"

    @classmethod
    def from_str(cls, value: Optional[str]) -> "GenerationProviderName":
        if not value:
            return cls.SIMULATED
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.SIMULATED


@dataclass
class GenerationResult:
    """What a provider hands back once a video artifact exists on disk."""

    video_path: str
    duration_seconds: float
    slide_count: int
    width: int
    height: int
    fps: int
    provider: str
    narration_voice: Optional[str] = None
    hardware_acceleration: Optional[str] = None  # e.g. "nvenc" or "cpu"

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "duration_seconds": self.duration_seconds,
            "slide_count": self.slide_count,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "provider": self.provider,
            "narration_voice": self.narration_voice,
            "hardware_acceleration": self.hardware_acceleration,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GenerationResult":
        return cls(
            video_path=d["video_path"],
            duration_seconds=d["duration_seconds"],
            slide_count=d["slide_count"],
            width=d["width"],
            height=d["height"],
            fps=d["fps"],
            provider=d["provider"],
            narration_voice=d.get("narration_voice"),
            hardware_acceleration=d.get("hardware_acceleration"),
        )


@dataclass
class Job:
    """A single requested video-generation job.

    This is pure state - it has no knowledge of *how* it is persisted
    (see persistence/job_store.py) or *how* a video gets produced
    (see generation/base.py). That separation is what requirement 13
    ("clear backend boundary for job state, generation logic, persistence,
    and artifacts") is about.
    """

    id: str
    query: str
    difficulty: DifficultyLevel
    provider: GenerationProviderName
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    stage: str = "queued"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    result: Optional[GenerationResult] = None

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def mark_generating(self, stage: str, progress: float) -> None:
        self.status = JobStatus.GENERATING
        self.stage = stage
        self.progress = progress
        self.touch()

    def mark_completed(self, result: GenerationResult) -> None:
        self.status = JobStatus.COMPLETED
        self.stage = "completed"
        self.progress = 1.0
        self.result = result
        self.error_message = None
        self.error_code = None
        self.touch()

    def mark_failed(self, message: str, code: str = "generation_failed") -> None:
        self.status = JobStatus.FAILED
        self.stage = "failed"
        self.error_message = message
        self.error_code = code
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "difficulty": self.difficulty.value,
            "provider": self.provider.value,
            "status": self.status.value,
            "progress": self.progress,
            "stage": self.stage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "result": self.result.to_dict() if self.result else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        return cls(
            id=d["id"],
            query=d["query"],
            difficulty=DifficultyLevel(d["difficulty"]),
            provider=GenerationProviderName(d["provider"]),
            status=JobStatus(d["status"]),
            progress=d.get("progress", 0.0),
            stage=d.get("stage", "queued"),
            created_at=d.get("created_at", utc_now_iso()),
            updated_at=d.get("updated_at", utc_now_iso()),
            error_message=d.get("error_message"),
            error_code=d.get("error_code"),
            result=GenerationResult.from_dict(d["result"]) if d.get("result") else None,
        )
