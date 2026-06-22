from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.schemas.user import UserCreate, UserRead, UserUpdate
from src.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/", response_model=UserRead, status_code=201)
async def create_user(data: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]) -> UserRead:
    user = await user_service.create_user(db, data)
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> UserRead:
    user = await user_service.get_user(db, user_id)
    return UserRead.model_validate(user)


@router.get("/", response_model=list[UserRead])
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[UserRead]:
    users = await user_service.get_users(db, offset=offset, limit=limit)
    return [UserRead.model_validate(u) for u in users]


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID, data: UserUpdate, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserRead:
    user = await user_service.update_user(db, user_id, data)
    return UserRead.model_validate(user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> None:
    await user_service.delete_user(db, user_id)
