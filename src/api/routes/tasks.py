from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.api.schemas.task import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse
from src.api.task_manager import IdempotencyConflictError, TaskEvent, TaskStatus, WorkerBusyError, task_manager
from src.config import settings
from src.harness.deadline import DeadlineExceededError
from src.harness.publish_guard import PublishRejectedError
from src.llm.errors import MESSAGES
from src.llm.redact import sanitize
from src.workflow.deterministic import run_deterministic_change
from src.workflow.failures import classify_exception, outcome_failed
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


def _assert_deterministic_codebase(original: str, resolved: str) -> None:
    """Demo writes may only target a real directory under .agentforge/."""
    agentforge = Path.cwd() / ".agentforge"
    if agentforge.is_symlink():
        raise HTTPException(status_code=400, detail="deterministic codebase must not be reached through a symlink")
    root = agentforge.resolve()
    resolved_path = Path(resolved)
    if not resolved_path.is_dir():
        raise HTTPException(status_code=400, detail="deterministic codebase must be a directory under .agentforge/")
    try:
        resolved_path.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="deterministic codebase must be a directory under .agentforge/",
        ) from None
    probe = Path(original)
    if not probe.is_absolute():
        probe = Path.cwd() / probe
    walking = Path(probe.anchor) if probe.is_absolute() else Path.cwd()
    parts = probe.parts[1:] if probe.is_absolute() else probe.parts
    for part in parts:
        walking = walking / part
        if walking.is_symlink():
            raise HTTPException(status_code=400, detail="deterministic codebase must not be reached through a symlink")


def _request_hash(intent: str, codebase_path: str, options: dict) -> str:
    body = {
        "change": options.get("change"),
        "codebase_path": codebase_path,
        "executor": options.get("executor", "pge"),
        "intent": intent,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


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
    if isinstance(idempotency_key, str) and not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="idempotency_key must be non-empty")
    if executor == "deterministic":
        if not settings.DETERMINISTIC_EXECUTOR:
            raise HTTPException(
                status_code=403,
                detail="deterministic executor is disabled; set DETERMINISTIC_EXECUTOR=1 to run the demo",
            )
        _assert_deterministic_codebase(request.codebase.get("path", "./toy-repo"), codebase_path)

    try:
        task = task_manager.create_task(
            intent=request.intent,
            codebase_path=codebase_path,
            idempotency_key=idempotency_key,
            request_hash=_request_hash(request.intent, codebase_path, options),
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"idempotency key already belongs to a different request: {exc.task_id}",
        ) from None
    except WorkerBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
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
    selected = (options or {}).get("executor", "pge")
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

        if selected == "deterministic":
            result = await run_deterministic_change(
                codebase_path=codebase_path,
                task_id=str(task_id),
                change=(options or {}).get("change") or {},
                on_published=lambda branch: task_manager.record_branch(task_id, branch),
            )
            await task_manager.emit_event(
                task_id,
                TaskEvent(event="step_update", data={"agent": "deterministic", "step": "demo_published"}),
            )
        else:
            result = await run_task(
                request=intent,
                codebase_path=codebase_path,
                task_id=str(task_id),
            )
            if not outcome_failed(result):
                await task_manager.emit_event(
                    task_id,
                    TaskEvent(event="step_update", data={"agent": "evaluator", "step": "complete"}),
                )

        trace = result.get("execution_trace", [])
        summary = result.get("execution_trace_summary")
        if summary is None:
            summary = len(trace) if isinstance(trace, list) else 0
        public = {
            "final_verdict": result.get("final_verdict"),
            "error_code": result.get("error_code"),
            "acceptance": result.get("acceptance"),
            "demo_completed": bool(result.get("demo_completed", False)),
            "executor": result.get("executor", "pge"),
            "note": result.get("note"),
            "degradations": result.get("degradations") or [],
            "sprint_contract": result.get("sprint_contract", []),
            "code_diff": result.get("code_diff", ""),
            "execution_trace_summary": summary,
            "cost_breakdown": result.get("cost_breakdown", {}),
            "trace": None if outcome_failed(result) else result.get("trace"),
            "result_branch": result.get("result_branch"),
        }
        if selected != "deterministic" and outcome_failed(result):
            message = sanitize(str(result.get("error") or result.get("note") or "The task failed."))
            code = result.get("error_code") or "task_failed"
            task_manager.set_error(task_id, message, code=code, result=public)
            await task_manager.emit_event(
                task_id,
                TaskEvent(event="error", data={"error": message, "error_code": code}),
            )
            return
        task_manager.set_result(task_id, public)

    except DeadlineExceededError as exc:
        branch = exc.branch or ""
        message = MESSAGES["execution_timeout"]
        if branch:
            message += f" Branch {branch} was left for inspection and is not an accepted result."
        task_manager.set_error(task_id, message, code="execution_timeout", result={
            "final_verdict": "FAIL",
            "error_code": "execution_timeout",
            "acceptance": "failed",
            "executor": selected,
            "demo_completed": False,
            "result_branch": branch,
            "residual_branch": branch,
            "note": message,
        })
    except PublishRejectedError as exc:
        branch = exc.branch or ""
        message = str(exc)
        if branch:
            message += f" Branch {branch} was left for inspection and is not an accepted result."
        task_manager.set_error(task_id, message, code=exc.code, result={
            "final_verdict": "FAIL",
            "error_code": exc.code,
            "acceptance": "failed",
            "executor": selected,
            "demo_completed": False,
            "result_branch": branch,
            "residual_branch": branch,
            "note": message,
        })
    except Exception as exc:
        if selected == "deterministic" and isinstance(exc, ValueError):
            code, message = "demo_rejected", sanitize(str(exc))
        else:
            code, message = classify_exception(exc)
        task_manager.set_error(task_id, message, code=code, result={
            "final_verdict": "FAIL",
            "error_code": code,
            "acceptance": "failed",
            "executor": (options or {}).get("executor", "pge"),
            "demo_completed": False,
            "note": message,
        })
        await task_manager.emit_event(
            task_id,
            TaskEvent(event="error", data={"error": message, "error_code": code}),
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
        error_code=task.error_code,
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
