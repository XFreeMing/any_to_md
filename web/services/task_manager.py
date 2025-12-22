"""Task state management for conversion jobs."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    """Task status enumeration."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Represents a conversion task."""

    task_id: str
    filename: str
    input_format: str
    output_format: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: str = ""
    download_filename: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update(
        self,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        download_filename: Optional[str] = None,
    ):
        """Update task state."""
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress = progress
        if message is not None:
            self.message = message
        if download_filename is not None:
            self.download_filename = download_filename
        self.updated_at = time.time()


class TaskManager:
    """Manages conversion task states in memory."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}

    def create_task(self, filename: str, input_format: str) -> Task:
        """Create a new task."""
        task_id = str(uuid.uuid4())
        task = Task(
            task_id=task_id,
            filename=filename,
            input_format=input_format,
            message="Waiting to start",
        )
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        download_filename: Optional[str] = None,
    ) -> Optional[Task]:
        """Update task state."""
        task = self._tasks.get(task_id)
        if task:
            task.update(status, progress, message, download_filename)
        return task

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def cleanup_old_tasks(self, max_age_seconds: int = 3600):
        """Remove tasks older than max_age_seconds."""
        now = time.time()
        expired = [
            task_id
            for task_id, task in self._tasks.items()
            if now - task.created_at > max_age_seconds
        ]
        for task_id in expired:
            del self._tasks[task_id]


# Global task manager instance
task_manager = TaskManager()
