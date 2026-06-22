from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient, sample_user_data: dict) -> None:
    resp = await client.post("/api/v1/users/", json=sample_user_data)
    assert resp.status_code == 201
    assert "password" not in resp.json()


@pytest.mark.asyncio
async def test_get_user(client: AsyncClient, sample_user_data: dict) -> None:
    create_resp = await client.post("/api/v1/users/", json=sample_user_data)
    user_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/users/{user_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_user_not_found(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, sample_user_data: dict) -> None:
    await client.post("/api/v1/users/", json=sample_user_data)
    resp = await client.get("/api/v1/users/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_update_user(client: AsyncClient, sample_user_data: dict) -> None:
    create_resp = await client.post("/api/v1/users/", json=sample_user_data)
    user_id = create_resp.json()["id"]
    resp = await client.put(f"/api/v1/users/{user_id}", json={"username": "updated"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "updated"


@pytest.mark.asyncio
async def test_delete_user(client: AsyncClient, sample_user_data: dict) -> None:
    create_resp = await client.post("/api/v1/users/", json=sample_user_data)
    user_id = create_resp.json()["id"]
    assert (await client.delete(f"/api/v1/users/{user_id}")).status_code == 204
    assert (await client.get(f"/api/v1/users/{user_id}")).status_code == 404


@pytest.mark.asyncio
async def test_create_user_duplicate_username(client: AsyncClient, sample_user_data: dict) -> None:
    await client.post("/api/v1/users/", json=sample_user_data)
    resp = await client.post("/api/v1/users/", json={**sample_user_data, "email": "other@x.com"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_user_duplicate_email(client: AsyncClient, sample_user_data: dict) -> None:
    await client.post("/api/v1/users/", json=sample_user_data)
    resp = await client.post("/api/v1/users/", json={**sample_user_data, "username": "other"})
    assert resp.status_code == 409
