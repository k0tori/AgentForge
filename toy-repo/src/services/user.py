from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import ConflictError, NotFoundError
from src.models.user import User
from src.schemas.user import UserCreate, UserUpdate


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    existing = await session.execute(
        select(User).where((User.username == data.username) | (User.email == data.email))
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("Username or email already exists")
    user = User(username=data.username, email=data.email, hashed_password=data.password)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def get_users(session: AsyncSession, offset: int = 0, limit: int = 20) -> list[User]:
    result = await session.execute(select(User).offset(offset).limit(limit))
    return list(result.scalars().all())


async def update_user(session: AsyncSession, user_id: uuid.UUID, data: UserUpdate) -> User:
    user = await get_user(session, user_id)
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        return user
    if "username" in update_data or "email" in update_data:
        from sqlalchemy import or_
        conditions = []
        if "username" in update_data:
            conditions.append(User.username == update_data["username"])
        if "email" in update_data:
            conditions.append(User.email == update_data["email"])
        existing = await session.execute(select(User).where(or_(*conditions)).where(User.id != user_id))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("Username or email already exists")
    for field, value in update_data.items():
        setattr(user, field, value)
    await session.flush()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    user = await get_user(session, user_id)
    await session.delete(user)
    await session.flush()
