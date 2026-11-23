from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.schemas.task import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse
from src.api.task_manager import TaskEvent, TaskStatus, task_manager
from src.workflow.deterministic import run_deterministic_change
from src.workflow.graph import run_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Store background task references to prevent garbage collection
_background_tasks: set[asyncio.Task] = set()


def _validate_codebase_path(path: str) -> str:
    """Validate codebase path to prevent path traversal attacks."""
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid path")
    resolved = Path(path).resolve()
    cwd = Path.cwd().resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path must be within project directory") from None
    return str(resolved)


def _sse_payload(data: object) -> str:
    """Serialize an SSE data field as JSON."""
    if isinstance(data, str):
        return data
    return json.dumps(data, default=str)


@router.post("/", response_model=TaskCreateResponse, status_code=202)
async def create_task(request: TaskCreateRequest) -> TaskCreateResponse:
    """Create a new task and start PGE execution."""
    task_manager.cleanup_old_tasks()

    # Validate and create task in manager
    codebase_path = request.codebase.get("path", "./toy-repo")
    codebase_path = _validate_codebase_path(codebase_path)

    options = request.options or {}
    executor = options.get("executor", "pge")
    if executor not in ("pge", "deterministic"):
        raise HTTPException(status_code=400, detail="executor must be pge or deterministic")
    idempotency_key = options.get("idempotency_key")
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise HTTPException(status_code=400, detail="idempotency_key must be a string")

    task = task_manager.create_task(
        intent=request.intent,
        codebase_path=codebase_path,
        idempotency_key=idempotency_key,
    )
    if task_manager.claim_execution(task.task_id):
        bg_task = asyncio.create_task(
            _execute_task(task.task_id, request.intent, codebase_path, options)
        )
        _background_tasks.add(bg_task)
        bg_task.add_done_callback(_background_tasks.discard)

    return TaskCreateResponse(
        task_id=task.task_id,
        status=task.status.value,
        sse_url=f"/api/v1/tasks/{task.task_id}/stream",
    )


async def _execute_task(
    task_id: uuid.UUID,
    intent: str,
    codebase_path: str,
    options: dict | None = None,
) -> None:
    """Execute a task in the background. Deterministic mode does not call a model."""
    try:
        # Update status to running
        task_manager.update_status(task_id, TaskStatus.RUNNING)
        await task_manager.emit_event(
            task_id,
            TaskEvent(event="status_change", data={"status": "running"}),
        )

        # Emit progress event
        await task_manager.emit_event(
            task_id,
            TaskEvent(event="step_update", data={"agent": "planner", "step": "starting"}),
        )

        selected = (options or {}).get("executor", "pge")
        if selected == "deterministic":
            result = await run_deterministic_change(
                codebase_path=codebase_path,
                task_id=str(task_id),
                change=(options or {}).get("change") or {},
            )
        else:
            result = await run_task(
                request=intent,
                codebase_path=codebase_path,
                task_id=str(task_id),
            )

        # Emit completion event
        await task_manager.emit_event(
            task_id,
            TaskEvent(event="step_update", data={"agent": "evaluator", "step": "complete"}),
        )

        # Set result
        trace = result.get("execution_trace", [])
        summary = result.get("execution_trace_summary")
        if summary is None:
            summary = len(trace) if isinstance(trace, list) else 0
        task_manager.set_result(task_id, {
            "final_verdict": result.get("final_verdict"),
            "executor": result.get("executor", "pge"),
            "note": result.get("note"),
            "sprint_contract": result.get("sprint_contract", []),
            "code_diff": result.get("code_diff", ""),
            "execution_trace_summary": summary,
            "cost_breakdown": result.get("cost_breakdown", {}),
            "trace": result.get("trace"),
            "result_branch": result.get("result_branch"),
        })

    except Exception as e:
        # Set error
        task_manager.set_error(task_id, str(e))
        await task_manager.emit_event(
            task_id,
            TaskEvent(event="error", data={"error": str(e)}),
        )


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: uuid.UUID) -> TaskStatusResponse:
    """Get task status and result."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        result=task.result,
        error=task.error,
    )


@router.get("/{task_id}/stream")
async def stream_task(task_id: uuid.UUID):
    """Stream task execution events via SSE."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        async for event in task_manager.event_stream(task_id):
            yield {
                "event": event.event,
                "data": _sse_payload(event.data),
            }

    return EventSourceResponse(event_generator())


@router.get("/{task_id}/events", response_model=list[TaskEvent])
async def get_task_events(task_id: uuid.UUID) -> list[TaskEvent]:
    """Get all events for a task."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.events
