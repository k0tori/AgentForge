from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class Note(SQLModel, table=True):
    """Note database model. Each note belongs to one user (one-to-many)."""

    __tablename__ = "notes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(max_length=200)
    content: str = Field(default="")
    owner_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
