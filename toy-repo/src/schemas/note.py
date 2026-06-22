from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    """Request body for creating a note."""

    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(default="")
    owner_id: uuid.UUID


class NoteUpdate(BaseModel):
    """Request body for updating a note (all fields optional)."""

    title: str | None = Field(None, min_length=1, max_length=200)
    content: str | None = None


class NoteRead(BaseModel):
    """Response body for a note."""

    id: uuid.UUID
    title: str
    content: str
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
