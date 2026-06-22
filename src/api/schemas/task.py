from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """Request body for creating a task."""

    intent: str = Field(..., description="The user's functional request")
    codebase: dict = Field(
        default_factory=lambda: {"type": "local_path", "path": "./toy-repo"},
        description="Codebase info: type and path",
    )
    options: dict[str, Any] | None = Field(None, description="Optional overrides")


class TaskCreateResponse(BaseModel):
    """Response after creating a task."""

    task_id: uuid.UUID
    status: str
    sse_url: str


class TaskStatusResponse(BaseModel):
    """Response for task status query."""

    task_id: uuid.UUID
    status: str
    result: dict[str, Any] | None = None
