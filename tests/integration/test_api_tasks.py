"""Tests for the tasks API endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.task_manager import TaskStatus, task_manager
from src.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_task_manager():
    """Clear task manager between tests."""
    task_manager._tasks.clear()
    task_manager._event_queues.clear()
    yield
    task_manager._tasks.clear()
    task_manager._event_queues.clear()


class TestCreateTask:
    """Test POST /api/v1/tasks/"""

    def test_create_task_returns_202(self, client):
        """Creating a task should return 202 Accepted."""
        response = client.post("/api/v1/tasks/", json={
            "intent": "Add a Tag resource",
            "codebase": {"type": "local_path", "path": "./toy-repo"},
        })
        assert response.status_code == 202

    def test_create_task_returns_task_id(self, client):
        """Response should contain a task_id."""
        response = client.post("/api/v1/tasks/", json={
            "intent": "Add a Tag resource",
        })
        data = response.json()
        assert "task_id" in data
        # Validate it's a UUID
        uuid.UUID(data["task_id"])

    def test_create_task_returns_sse_url(self, client):
        """Response should contain sse_url."""
        response = client.post("/api/v1/tasks/", json={
            "intent": "Add a Tag resource",
        })
        data = response.json()
        assert "sse_url" in data
        assert "/stream" in data["sse_url"]

    def test_create_task_returns_pending_status(self, client):
        """Initial status should be pending."""
        response = client.post("/api/v1/tasks/", json={
            "intent": "Add a Tag resource",
        })
        data = response.json()
        assert data["status"] == "pending"


class TestGetTask:
    """Test GET /api/v1/tasks/{task_id}"""

    def test_get_existing_task(self, client):
        """Should return task details."""
        # Create a task first
        create_response = client.post("/api/v1/tasks/", json={
            "intent": "Add a Tag resource",
        })
        task_id = create_response.json()["task_id"]

        # Get the task
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id

    def test_get_nonexistent_task(self, client):
        """Should return 404 for non-existent task."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/v1/tasks/{fake_id}")
        assert response.status_code == 404


class TestTaskManager:
    """Test TaskManager directly."""

    def test_create_task(self):
        """Should create a task with correct fields."""
        manager = task_manager
        task = manager.create_task("test intent", "./toy-repo")
        assert task.intent == "test intent"
        assert task.codebase_path == "./toy-repo"
        assert task.status == TaskStatus.PENDING

    def test_get_task(self):
        """Should retrieve task by ID."""
        manager = task_manager
        task = manager.create_task("test")
        retrieved = manager.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    def test_update_status(self):
        """Should update task status."""
        manager = task_manager
        task = manager.create_task("test")
        manager.update_status(task.task_id, TaskStatus.RUNNING)
        assert manager.get_task(task.task_id).status == TaskStatus.RUNNING

    def test_set_result(self):
        """Should set result and mark as completed."""
        manager = task_manager
        task = manager.create_task("test")
        result = {"verdict": "PASS"}
        manager.set_result(task.task_id, result)
        updated = manager.get_task(task.task_id)
        assert updated.status == TaskStatus.COMPLETED
        assert updated.result == result

    def test_set_error(self):
        """Should set error and mark as failed."""
        manager = task_manager
        task = manager.create_task("test")
        manager.set_error(task.task_id, "Something went wrong")
        updated = manager.get_task(task.task_id)
        assert updated.status == TaskStatus.FAILED
        assert updated.error == "Something went wrong"
