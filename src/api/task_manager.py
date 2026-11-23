"""Task manager for tracking task execution.

Durable rows live in SQLite. The process cache is only for the live SSE queue.
In-memory TTL cleanup applies when no database is bound.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


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
    idempotency_key: str | None = None


class TaskManager:
    """Manages task execution and state with TTL-based cleanup."""

    # Tasks completed before this duration are eligible for cleanup
    CLEANUP_THRESHOLD = timedelta(hours=1)

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, TaskState] = {}
        self._event_queues: dict[uuid.UUID, asyncio.Queue[TaskEvent]] = {}
        self._store = None
        self._started: set[uuid.UUID] = set()

    def use_memory(self) -> None:
        """Drop the persistent store and the in-memory cache. Used by tests."""
        if self._store is not None:
            self._store.close()
            self._store = None
        self._tasks.clear()
        self._event_queues.clear()
        self._started: set[uuid.UUID] = set()

    def bind(self, path: str) -> int:
        """Load tasks from SQLite and mark in-flight work interrupted.

        A restarted process cannot safely resume a PGE run, so pending and
        running rows become interrupted instead of staying running.
        """
        from src.api.task_store import SqliteTaskStore

        self.use_memory()
        self._store = SqliteTaskStore(path)
        for task in self._store.load_all():
            self._tasks[task.task_id] = task
            self._event_queues[task.task_id] = asyncio.Queue()
        return self.mark_interrupted()

    def mark_interrupted(self) -> int:
        """Fail closed on tasks that were still executing when the process stopped."""
        count = 0
        for task in list(self._tasks.values()):
            if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                continue
            task.status = TaskStatus.INTERRUPTED
            task.error = (
                "Process restarted while this task was in progress. "
                "It was not resumed and did not succeed."
            )
            task.completed_at = datetime.now(UTC)
            task.events.append(TaskEvent(event="interrupted", data={"status": TaskStatus.INTERRUPTED.value}))
            self._persist(task)
            count += 1
        return count

    def _persist(self, task: TaskState) -> None:
        if self._store is not None:
            self._store.upsert(task, task.idempotency_key)

    def cleanup_old_tasks(self) -> int:
        """Remove tasks completed more than CLEANUP_THRESHOLD ago.

        Persisted tasks stay until the operator deletes the database file.
        Evicting them from memory would make GET 404 while a restart loaded
        the same row again.

        Returns:
            Number of tasks removed
        """
        if self._store is not None:
            return 0
        now = datetime.now(UTC)
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

    def create_task(
        self,
        intent: str,
        codebase_path: str = "./toy-repo",
        idempotency_key: str | None = None,
    ) -> TaskState:
        """Create a task, or return the existing row for a repeated idempotency key."""
        if idempotency_key and self._store is not None:
            existing_id = self._store.find_id_by_idempotency_key(idempotency_key)
            if existing_id is not None:
                found = self._remember(existing_id)
                if found is not None:
                    return found
        task_id = uuid.uuid4()
        task = TaskState(
            task_id=task_id,
            intent=intent,
            codebase_path=codebase_path,
            idempotency_key=idempotency_key,
        )
        self._tasks[task_id] = task
        self._event_queues[task_id] = asyncio.Queue()
        try:
            self._persist(task)
        except sqlite3.IntegrityError:
            self._tasks.pop(task_id, None)
            self._event_queues.pop(task_id, None)
            if idempotency_key and self._store is not None:
                existing_id = self._store.find_id_by_idempotency_key(idempotency_key)
                if existing_id is not None:
                    found = self._remember(existing_id)
                    if found is not None:
                        return found
            raise
        return task

    def _remember(self, task_id: str) -> TaskState | None:
        parsed = uuid.UUID(task_id)
        found = self._tasks.get(parsed)
        if found is not None:
            return found
        if self._store is None:
            return None
        found = self._store.get(task_id)
        if found is None:
            return None
        self._tasks[found.task_id] = found
        self._event_queues[found.task_id] = asyncio.Queue()
        return found

    def claim_execution(self, task_id: uuid.UUID) -> bool:
        """Return True only for the first caller that starts this task."""
        if task_id in self._started:
            return False
        task = self._tasks.get(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return False
        if self._store is not None and not self._store.try_claim(str(task_id)):
            task.status = TaskStatus.RUNNING
            return False
        self._started.add(task_id)
        return True

    def get_task(self, task_id: uuid.UUID) -> TaskState | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def update_status(self, task_id: uuid.UUID, status: TaskStatus) -> None:
        """Update task status."""
        if task_id in self._tasks:
            self._tasks[task_id].status = status
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED):
                self._tasks[task_id].completed_at = datetime.now(UTC)
            self._persist(self._tasks[task_id])

    def set_result(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        """Set task result."""
        if task_id in self._tasks:
            self._tasks[task_id].result = result
            self._tasks[task_id].status = TaskStatus.COMPLETED
            self._tasks[task_id].completed_at = datetime.now(UTC)
            self._persist(self._tasks[task_id])

    def set_error(self, task_id: uuid.UUID, error: str) -> None:
        """Set task error."""
        if task_id in self._tasks:
            self._tasks[task_id].error = error
            self._tasks[task_id].status = TaskStatus.FAILED
            self._tasks[task_id].completed_at = datetime.now(UTC)
            self._persist(self._tasks[task_id])

    async def emit_event(self, task_id: uuid.UUID, event: TaskEvent) -> None:
        """Emit an SSE event for a task."""
        if task_id in self._tasks:
            self._tasks[task_id].events.append(event)
            self._persist(self._tasks[task_id])
        if task_id in self._event_queues:
            await self._event_queues[task_id].put(event)

    async def event_stream(self, task_id: uuid.UUID):
        """Yield SSE events for a task."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED):
            yield TaskEvent(
                event="task_complete",
                data={
                    "task_id": str(task_id),
                    "status": task.status.value,
                    "result": task.result,
                    "error": task.error,
                },
            )
            return

        queue = self._event_queues[task_id]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield event
            except TimeoutError:
                # Send heartbeat
                yield TaskEvent(event="heartbeat", data={"task_id": str(task_id)})

            # Check if task is done
            task = self._tasks.get(task_id)
            if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED):
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
