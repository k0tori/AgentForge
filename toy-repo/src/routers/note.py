from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.schemas.note import NoteCreate, NoteRead, NoteUpdate
from src.services import note as note_service

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("/", response_model=NoteRead, status_code=201)
async def create_note(data: NoteCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> NoteRead:
    note = await note_service.create_note(db, data)
    return NoteRead.model_validate(note)


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(note_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> NoteRead:
    note = await note_service.get_note(db, note_id)
    return NoteRead.model_validate(note)


@router.get("/", response_model=list[NoteRead])
async def list_notes(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[NoteRead]:
    notes = await note_service.get_notes(db, offset=offset, limit=limit)
    return [NoteRead.model_validate(n) for n in notes]


@router.put("/{note_id}", response_model=NoteRead)
async def update_note(
    note_id: uuid.UUID, data: NoteUpdate, db: Annotated[AsyncSession, Depends(get_db)]
) -> NoteRead:
    note = await note_service.update_note(db, note_id, data)
    return NoteRead.model_validate(note)


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> None:
    await note_service.delete_note(db, note_id)
