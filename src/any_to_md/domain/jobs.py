"""Persistent conversion job state shared by every interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    PARTIAL = "partial"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.COMPLETED_WITH_WARNINGS, self.PARTIAL, self.FAILED}

    @property
    def downloadable(self) -> bool:
        return self in {self.COMPLETED, self.COMPLETED_WITH_WARNINGS, self.PARTIAL}


class JobConflict(RuntimeError):
    """A job was already claimed, finished, or lost its execution lease."""


@dataclass(frozen=True)
class Job:
    job_id: str
    filename: str
    input_format: str
    status: JobStatus
    targets: tuple[str, ...]
    output_format: str | None
    source_id: str | None
    input_key: str | None
    progress: int
    message: str
    created_at: str
    finished_at: str | None
    report_key: str | None
    export_id: str | None
    download_relative: str | None
    storage_key: str | None

