from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import NotFoundError, ValidationError
from src.models.note import Note
from src.models.user import User
from src.schemas.note import NoteCreate, NoteUpdate


async def create_note(session: AsyncSession, data: NoteCreate) -> Note:
    owner = await session.execute(select(User).where(User.id == data.owner_id))
    if owner.scalar_one_or_none() is None:
        raise ValidationError(f"User {data.owner_id} does not exist")
    note = Note(title=data.title, content=data.content, owner_id=data.owner_id)
    session.add(note)
    await session.flush()
    await session.refresh(note)
    return note


async def get_note(session: AsyncSession, note_id: uuid.UUID) -> Note:
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note is None:
        raise NotFoundError(f"Note {note_id} not found")
    return note


async def get_notes(session: AsyncSession, offset: int = 0, limit: int = 20) -> list[Note]:
    result = await session.execute(select(Note).offset(offset).limit(limit))
    return list(result.scalars().all())


async def update_note(session: AsyncSession, note_id: uuid.UUID, data: NoteUpdate) -> Note:
    note = await get_note(session, note_id)
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        return note
    for field, value in update_data.items():
        setattr(note, field, value)
    await session.flush()
    await session.refresh(note)
    return note


async def delete_note(session: AsyncSession, note_id: uuid.UUID) -> None:
    note = await get_note(session, note_id)
    await session.delete(note)
    await session.flush()
