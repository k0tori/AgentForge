from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.schemas.task import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse
from src.api.task_manager import TaskEvent, TaskStatus, task_manager
from src.workflow.graph import run_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _validate_codebase_path(path: str) -> str:
    """Validate codebase path to prevent path traversal attacks."""
    # Resolve to absolute path
    resolved = Path(path).resolve()

    # Check for path traversal attempts
    if ".." in path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    # For safety, only allow relative paths within current working directory
    cwd = Path.cwd().resolve()
    if not str(resolved).startswith(str(cwd)):
        raise HTTPException(status_code=400, detail="Path must be within project directory")

    return str(resolved)


@router.post("/", response_model=TaskCreateResponse, status_code=202)
async def create_task(request: TaskCreateRequest) -> TaskCreateResponse:
    """Create a new task and start PGE execution."""
    # Validate and create task in manager
    codebase_path = request.codebase.get("path", "./toy-repo")
    codebase_path = _validate_codebase_path(codebase_path)

    task = task_manager.create_task(
        intent=request.intent,
        codebase_path=codebase_path,
    )

    # Start background execution
    asyncio.create_task(_execute_task(task.task_id, request.intent, codebase_path))

    return TaskCreateResponse(
        task_id=task.task_id,
        status=task.status.value,
        sse_url=f"/api/v1/tasks/{task.task_id}/stream",
    )


async def _execute_task(task_id: uuid.UUID, intent: str, codebase_path: str) -> None:
    """Execute PGE task in background."""
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

        # Run PGE flow
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
        task_manager.set_result(task_id, {
            "final_verdict": result.get("final_verdict"),
            "sprint_contract": result.get("sprint_contract", []),
            "code_diff": result.get("code_diff", ""),
            "execution_trace_summary": len(result.get("execution_trace", [])),
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
                "data": event.data.model_dump_json() if hasattr(event.data, "model_dump_json") else str(event.data),
            }

    return EventSourceResponse(event_generator())


@router.get("/{task_id}/events", response_model=list[TaskEvent])
async def get_task_events(task_id: uuid.UUID) -> list[TaskEvent]:
    """Get all events for a task."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return task.events
