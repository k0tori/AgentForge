from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


async def _create_user(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/users/", json={
        "username": f"owner_{uuid.uuid4().hex[:8]}",
        "email": f"owner_{uuid.uuid4().hex[:8]}@example.com",
        "password": "password123",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_note(client: AsyncClient, sample_note_data: dict) -> None:
    owner_id = await _create_user(client)
    resp = await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": owner_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_get_note(client: AsyncClient, sample_note_data: dict) -> None:
    owner_id = await _create_user(client)
    create_resp = await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": owner_id})
    note_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/notes/{note_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_note_not_found(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/notes/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_notes(client: AsyncClient, sample_note_data: dict) -> None:
    owner_id = await _create_user(client)
    await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": owner_id})
    resp = await client.get("/api/v1/notes/")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_update_note(client: AsyncClient, sample_note_data: dict) -> None:
    owner_id = await _create_user(client)
    create_resp = await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": owner_id})
    note_id = create_resp.json()["id"]
    resp = await client.put(f"/api/v1/notes/{note_id}", json={"title": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated"


@pytest.mark.asyncio
async def test_delete_note(client: AsyncClient, sample_note_data: dict) -> None:
    owner_id = await _create_user(client)
    create_resp = await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": owner_id})
    note_id = create_resp.json()["id"]
    assert (await client.delete(f"/api/v1/notes/{note_id}")).status_code == 204
    assert (await client.get(f"/api/v1/notes/{note_id}")).status_code == 404


@pytest.mark.asyncio
async def test_create_note_invalid_owner(client: AsyncClient, sample_note_data: dict) -> None:
    resp = await client.post("/api/v1/notes/", json={**sample_note_data, "owner_id": str(uuid.uuid4())})
    assert resp.status_code == 422
