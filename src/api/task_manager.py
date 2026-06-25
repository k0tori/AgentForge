"""Task manager for tracking PGE task execution.

Stores task state in memory (for now) and provides SSE streaming.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    timestamp: datetime = Field(default_factory=datetime.now)


class TaskState(BaseModel):
    """Full task state."""

    task_id: uuid.UUID
    status: TaskStatus = TaskStatus.PENDING
    intent: str = ""
    codebase_path: str = "./toy-repo"
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[TaskEvent] = Field(default_factory=list)


class TaskManager:
    """Manages task execution and state."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, TaskState] = {}
        self._event_queues: dict[uuid.UUID, asyncio.Queue[TaskEvent]] = {}

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
                self._tasks[task_id].completed_at = datetime.now()

    def set_result(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        """Set task result."""
        if task_id in self._tasks:
            self._tasks[task_id].result = result
            self._tasks[task_id].status = TaskStatus.COMPLETED
            self._tasks[task_id].completed_at = datetime.now()

    def set_error(self, task_id: uuid.UUID, error: str) -> None:
        """Set task error."""
        if task_id in self._tasks:
            self._tasks[task_id].error = error
            self._tasks[task_id].status = TaskStatus.FAILED
            self._tasks[task_id].completed_at = datetime.now()

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
