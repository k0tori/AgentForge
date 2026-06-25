"""Task manager for tracking PGE task execution.

Stores task state in memory with TTL-based cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskEvent(BaseModel):
    """SSE event for task progress."""

    event: str
    data: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskState(BaseModel):
    """Full task state."""

    task_id: uuid.UUID
    status: TaskStatus = TaskStatus.PENDING
    intent: str = ""
    codebase_path: str = "./toy-repo"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[TaskEvent] = Field(default_factory=list)


class TaskManager:
    """Manages task execution and state with TTL-based cleanup."""

    # Tasks completed before this duration are eligible for cleanup
    CLEANUP_THRESHOLD = timedelta(hours=1)

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, TaskState] = {}
        self._event_queues: dict[uuid.UUID, asyncio.Queue[TaskEvent]] = {}

    def cleanup_old_tasks(self) -> int:
        """Remove tasks completed more than CLEANUP_THRESHOLD ago.

        Returns:
            Number of tasks removed
        """
        now = datetime.now()
        to_remove = []

        for task_id, task in self._tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                if task.completed_at and (now - task.completed_at) > self.CLEANUP_THRESHOLD:
                    to_remove.append(task_id)

        for task_id in to_remove:
            del self._tasks[task_id]
            self._event_queues.pop(task_id, None)

        if to_remove:
            logger.info("Cleaned up %d old tasks", len(to_remove))

        return len(to_remove)

    def create_task(self, intent: str, codebase_path: str = "./toy-repo") -> TaskState:
        """Create a new task."""
        task_id = uuid.uuid4()
        task = TaskState(
            task_id=task_id,
            intent=intent,
            codebase_path=codebase_path,
        )
        self._tasks[task_id] = task
        self._event_queues[task_id] = asyncio.Queue()
        return task

    def get_task(self, task_id: uuid.UUID) -> TaskState | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def update_status(self, task_id: uuid.UUID, status: TaskStatus) -> None:
        """Update task status."""
        if task_id in self._tasks:
            self._tasks[task_id].status = status
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                self._tasks[task_id].completed_at = datetime.now(UTC)

    def set_result(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        """Set task result."""
        if task_id in self._tasks:
            self._tasks[task_id].result = result
            self._tasks[task_id].status = TaskStatus.COMPLETED
            self._tasks[task_id].completed_at = datetime.now(UTC)

    def set_error(self, task_id: uuid.UUID, error: str) -> None:
        """Set task error."""
        if task_id in self._tasks:
            self._tasks[task_id].error = error
            self._tasks[task_id].status = TaskStatus.FAILED
            self._tasks[task_id].completed_at = datetime.now(UTC)

    async def emit_event(self, task_id: uuid.UUID, event: TaskEvent) -> None:
        """Emit an SSE event for a task."""
        if task_id in self._tasks:
            self._tasks[task_id].events.append(event)
        if task_id in self._event_queues:
            await self._event_queues[task_id].put(event)

    async def event_stream(self, task_id: uuid.UUID):
        """Yield SSE events for a task."""
        if task_id not in self._event_queues:
            return

        queue = self._event_queues[task_id]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield event
            except asyncio.TimeoutError:
                # Send heartbeat
                yield TaskEvent(event="heartbeat", data={"task_id": str(task_id)})

            # Check if task is done
            task = self._tasks.get(task_id)
            if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                # Yield final event
                yield TaskEvent(
                    event="task_complete",
                    data={
                        "task_id": str(task_id),
                        "status": task.status.value,
                        "result": task.result,
                        "error": task.error,
                    },
                )
                break


# Global task manager instance
task_manager = TaskManager()
