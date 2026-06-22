from __future__ import annotations

import uuid

from fastapi import APIRouter
from src.api.schemas.task import TaskCreateRequest, TaskCreateResponse, TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/", response_model=TaskCreateResponse, status_code=202)
async def create_task(request: TaskCreateRequest) -> TaskCreateResponse:
    """Create a new task. PGE execution will be wired in Phase 2."""
    task_id = uuid.uuid4()
    return TaskCreateResponse(
        task_id=task_id,
        status="pending",
        sse_url=f"/api/v1/tasks/{task_id}/stream",
    )


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: uuid.UUID) -> TaskStatusResponse:
    """Get task status. Full implementation in Phase 2."""
    return TaskStatusResponse(task_id=task_id, status="not_found")
