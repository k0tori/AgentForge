"""Default PGE path failures. Fault injection only: no paid model and no container.

Real container evidence stays in tests/sandbox/test_docker_sandbox.py.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.task_manager import task_manager
from src.config import settings
from src.main import app


@pytest.fixture
def task_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "tasks.sqlite"
    monkeypatch.setattr(settings, "TASK_DB_PATH", str(database))
    task_manager.bind(str(database))
    yield database
    task_manager.use_memory()


async def _wait(client: AsyncClient, task_id: str) -> dict:
    last = {}
    for _ in range(50):
        response = await client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"completed", "failed", "interrupted"}:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(last)


def _body() -> dict:
    return {"intent": "Add a tag resource", "codebase": {"type": "local_path", "path": "./toy-repo"}}


@pytest.mark.asyncio
async def test_missing_key_is_persisted_failed_and_visible_on_stream(task_db: Path, monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5) as client:
        created = await client.post("/api/v1/tasks/", json=_body())
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        finished = await _wait(client, task_id)
        async with client.stream("GET", f"/api/v1/tasks/{task_id}/stream") as stream:
            payload = (await stream.aread()).decode()
    assert finished["status"] == "failed"
    assert finished["error_code"] == "missing_model_config"
    assert finished["result"]["final_verdict"] == "FAIL"
    assert finished["result"]["final_verdict"] != "PASS"
    assert "task_complete" in payload
    assert "missing_model_config" in payload
    assert "failed" in payload

    task_manager.use_memory()
    task_manager.bind(str(task_db))
    restored = task_manager.get_task(uuid.UUID(task_id))
    assert restored is not None
    assert restored.status.value == "failed"
    assert restored.error_code == "missing_model_config"
    assert restored.result["final_verdict"] == "FAIL"


@pytest.mark.asyncio
async def test_missing_docker_and_image_fail_without_leaking_the_key(task_db: Path, monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
    monkeypatch.setattr("src.harness.sandbox.runtime.docker_ready", lambda: False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5) as client:
        created = await client.post("/api/v1/tasks/", json=_body())
        docker_failed = await _wait(client, created.json()["task_id"])
    assert docker_failed["status"] == "failed"
    assert docker_failed["error_code"] == "sandbox_unavailable"
    assert "sk-injected" not in str(docker_failed)

    monkeypatch.setattr("src.harness.sandbox.runtime.docker_ready", lambda: True)
    monkeypatch.setattr("src.harness.sandbox.runtime.image_present", lambda _image: False)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5) as client:
        created = await client.post("/api/v1/tasks/", json=_body())
        image_failed = await _wait(client, created.json()["task_id"])
    assert image_failed["error_code"] == "sandbox_image_missing"
    assert image_failed["status"] == "failed"
    assert "sk-injected" not in str(image_failed)
    assert "profile sandbox build" in image_failed["error"]


@pytest.mark.asyncio
async def test_execution_timeout_fails_the_task(task_db: Path, monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "host")
    monkeypatch.setattr(settings, "TIMEOUT_SECONDS", 0.2)

    async def ready(_path: str) -> list:
        return []

    class _Slow:
        async def ainvoke(self, state):
            await asyncio.sleep(2)
            return state

    monkeypatch.setattr("src.workflow.preflight.assess_retrieval", ready)
    monkeypatch.setattr("src.workflow.graph.get_compiled_graph", lambda: _Slow())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5) as client:
        created = await client.post("/api/v1/tasks/", json=_body())
        finished = await _wait(client, created.json()["task_id"])
    assert finished["status"] == "failed"
    assert finished["error_code"] == "execution_timeout"
    assert finished["result"]["final_verdict"] == "FAIL"
    assert "sk-injected" not in str(finished)


@pytest.mark.asyncio
async def test_retrieval_degradation_is_visible_on_get(task_db: Path, monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "host")

    async def down() -> bool:
        return False

    class _Pass:
        async def ainvoke(self, state):
            return {**state, "final_verdict": "PASS", "error": None}

    monkeypatch.setattr("src.workflow.preflight.storage_available", down)
    monkeypatch.setattr("src.workflow.graph.get_compiled_graph", lambda: _Pass())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5) as client:
        created = await client.post("/api/v1/tasks/", json=_body())
        finished = await _wait(client, created.json()["task_id"])
    assert finished["status"] == "completed"
    assert finished["result"]["degradations"][0]["code"] == "retrieval_unavailable"
    assert "sk-injected" not in str(finished)
    assert "://" not in finished["result"]["degradations"][0]["message"]
