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


class WorkerBusyError(RuntimeError):
    """Another live process already owns the task database."""


class IdempotencyConflictError(RuntimeError):
    """The idempotency key exists and the request body does not match."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"idempotency key already belongs to task {task_id}")
        self.task_id = task_id


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
    error_code: str | None = None
    events: list[TaskEvent] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_hash: str | None = None


class TaskManager:
    """Manages task execution and state with TTL-based cleanup."""

    # Tasks completed before this duration are eligible for cleanup
    CLEANUP_THRESHOLD = timedelta(hours=1)

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, TaskState] = {}
        self._event_queues: dict[uuid.UUID, asyncio.Queue[TaskEvent]] = {}
        self._store = None
        self._started: set[uuid.UUID] = set()
        self._owner = True

    def use_memory(self) -> None:
        """Drop the persistent store and the in-memory cache. Used by tests."""
        if self._store is not None:
            self._store.close()
            self._store = None
        self._tasks.clear()
        self._event_queues.clear()
        self._started = set()
        self._owner = True

    def bind(self, path: str, *, owner: bool = True) -> int:
        """Open the task database.

        The owner is the only process that may execute tasks. It takes an
        exclusive flock. A dead owner's lock is released by the kernel, so the
        next owner marks that process's pending and running rows interrupted.
        A second live process must pass owner=False to read. It does not
        interrupt anything and it cannot execute.
        """
        from src.api.task_store import SqliteTaskStore

        self.use_memory()
        store = SqliteTaskStore(path)
        if owner and not store.acquire_owner():
            store.close()
            raise WorkerBusyError(path)
        self._store = store
        self._owner = owner
        if not owner:
            return 0
        for task in store.load_all():
            self._tasks[task.task_id] = task
            self._event_queues[task.task_id] = asyncio.Queue()
        return self.mark_interrupted()

    def mark_interrupted(self) -> int:
        """Fail closed on tasks that were still executing when the previous owner stopped."""
        from src.harness.workspace import published_branch_name

        count = 0
        for task in list(self._tasks.values()):
            if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                continue
            expected = published_branch_name(str(task.task_id))
            recorded = (task.result or {}).get("result_branch") or expected
            task.status = TaskStatus.INTERRUPTED
            task.error = (
                "Process restarted while this task was in progress. "
                "It was not resumed and did not succeed. "
                f"Branch {recorded} is not an accepted result."
            )
            task.completed_at = datetime.now(UTC)
            task.events.append(TaskEvent(event="interrupted", data={"status": TaskStatus.INTERRUPTED.value}))
            self._persist(task)
            count += 1
        return count

    def _require_owner(self) -> None:
        if self._store is not None and not self._owner:
            raise WorkerBusyError("this process is not the task database owner")

    def _persist(self, task: TaskState) -> None:
        if self._store is not None:
            self._store.upsert(task)

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
        request_hash: str | None = None,
    ) -> TaskState:
        """Create a task, or return the existing row when the key and body match."""
        self._require_owner()
        if idempotency_key and self._store is not None:
            existing = self._match_idempotency(idempotency_key, request_hash)
            if existing is not None:
                return existing
        task_id = uuid.uuid4()
        task = TaskState(
            task_id=task_id,
            intent=intent,
            codebase_path=codebase_path,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self._tasks[task_id] = task
        self._event_queues[task_id] = asyncio.Queue()
        try:
            self._persist(task)
        except sqlite3.IntegrityError:
            self._tasks.pop(task_id, None)
            self._event_queues.pop(task_id, None)
            if idempotency_key and self._store is not None:
                existing = self._match_idempotency(idempotency_key, request_hash)
                if existing is not None:
                    return existing
            raise
        return task

    def _match_idempotency(self, key: str, request_hash: str | None) -> TaskState | None:
        if self._store is None:
            return None
        found = self._store.find_by_idempotency_key(key)
        if found is None:
            return None
        existing_id, existing_hash = found
        if request_hash != existing_hash:
            raise IdempotencyConflictError(existing_id)
        return self._remember(existing_id)

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
        """Return True only for the first owner call that starts this task."""
        if self._store is not None and not self._owner:
            return False
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
        """Return the task. A bound store is read from SQLite, not a stale cache."""
        if self._store is not None:
            fresh = self._store.get(str(task_id))
            if fresh is None:
                self._tasks.pop(task_id, None)
                return None
            self._tasks[fresh.task_id] = fresh
            self._event_queues.setdefault(fresh.task_id, asyncio.Queue())
            return fresh
        return self._tasks.get(task_id)

    def record_branch(self, task_id: uuid.UUID, branch: str) -> None:
        """Remember a branch that git already created while the task is still running.

        Status stays running. A later crash must not turn this into success.
        """
        self._require_owner()
        task = self._tasks.get(task_id)
        if task is None or task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED):
            return
        payload = dict(task.result or {})
        payload["result_branch"] = branch
        payload["acceptance"] = "not_run"
        payload["demo_completed"] = False
        task.result = payload
        self._persist(task)

    def update_status(self, task_id: uuid.UUID, status: TaskStatus) -> None:
        """Update task status."""
        self._require_owner()
        if task_id in self._tasks:
            self._tasks[task_id].status = status
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.INTERRUPTED):
                self._tasks[task_id].completed_at = datetime.now(UTC)
            self._persist(self._tasks[task_id])

    def set_result(self, task_id: uuid.UUID, result: dict[str, Any]) -> None:
        """Set task result. An interrupted task stays interrupted."""
        self._require_owner()
        if task_id not in self._tasks:
            return
        if self._tasks[task_id].status == TaskStatus.INTERRUPTED:
            return
        self._tasks[task_id].result = result
        self._tasks[task_id].status = TaskStatus.COMPLETED
        self._tasks[task_id].completed_at = datetime.now(UTC)
        self._persist(self._tasks[task_id])

    def set_error(
        self,
        task_id: uuid.UUID,
        error: str,
        *,
        code: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Record a failure. An interrupted task stays interrupted."""
        from src.llm.redact import sanitize

        self._require_owner()
        task = self._tasks.get(task_id)
        if task is None or task.status == TaskStatus.INTERRUPTED:
            return
        task.error = sanitize(error)
        task.error_code = code
        if result is not None:
            task.result = result
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(UTC)
        self._persist(task)

    async def emit_event(self, task_id: uuid.UUID, event: TaskEvent) -> None:
        """Emit an SSE event for a task."""
        self._require_owner()
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
                    "error_code": task.error_code,
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
                    "error_code": task.error_code,
                },
            )
            break


# Global task manager instance
task_manager = TaskManager()
