"""Task lifecycle without a paid model.

The deterministic executor writes a real file and publishes a git branch.
It does not call an LLM and does not measure model quality.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.task_manager import TaskManager, TaskStatus
from src.main import app


def _copy_toy_repo() -> Path:
    destination = Path.cwd() / ".agentforge" / "it" / uuid.uuid4().hex
    shutil.copytree(Path.cwd() / "toy-repo", destination, ignore=shutil.ignore_patterns(".git"))
    return destination


def _payload(codebase: Path, filename: str, key: str | None = None) -> dict:
    options: dict = {
        "executor": "deterministic",
        "change": {
            "path": filename,
            "content": f'def marker() -> str:\n    return "{filename}"\n',
        },
    }
    if key is not None:
        options["idempotency_key"] = key
    return {
        "intent": f"Add {filename} without using a model",
        "codebase": {"type": "local_path", "path": str(codebase)},
        "options": options,
    }


async def _wait_terminal(client: AsyncClient, task_id: str) -> dict:
    last = {}
    for _ in range(100):
        response = await client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"completed", "failed", "interrupted"}:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"task stayed non-terminal: {last}")


@pytest.fixture
def task_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.api.task_manager import task_manager

    database = tmp_path / "tasks.sqlite"
    monkeypatch.setattr("src.config.settings.TASK_DB_PATH", str(database))
    task_manager.bind(str(database))
    yield database
    task_manager.use_memory()


@pytest.mark.asyncio
async def test_deterministic_task_publishes_a_branch_and_keeps_the_worktree(task_db: Path):
    codebase = _copy_toy_repo()
    original = (codebase / "src" / "main.py").read_text(encoding="utf-8")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/ping.py"))
            assert created.status_code == 202
            body = created.json()
            finished = await _wait_terminal(client, body["task_id"])
        assert finished["status"] == "completed"
        assert finished["error"] is None
        result = finished["result"]
        assert result["executor"] == "deterministic"
        assert "does not validate model quality" in result["note"]
        branch = result["result_branch"]
        assert branch.startswith("agentforge/")
        assert "def marker" in result["code_diff"]
        assert (codebase / "src" / "main.py").read_text(encoding="utf-8") == original
        assert not (codebase / "src" / "ping.py").exists()
        committed = subprocess.check_output(
            ["git", "-C", str(codebase), "show", f"{branch}:src/ping.py"],
            text=True,
        )
        assert 'return "src/ping.py"' in committed
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_failed_change_does_not_publish_or_edit_the_worktree(task_db: Path):
    codebase = _copy_toy_repo()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/tasks/",
                json={
                    "intent": "escape the workspace",
                    "codebase": {"path": str(codebase)},
                    "options": {
                        "executor": "deterministic",
                        "change": {"path": "../escape.py", "content": "x = 1\n"},
                    },
                },
            )
            finished = await _wait_terminal(client, created.json()["task_id"])
        assert finished["status"] == "failed"
        assert "relative .py" in finished["error"]
        assert finished["result"] is None
        assert not (codebase.parent / "escape.py").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_reuses_one_task(task_db: Path):
    codebase = _copy_toy_repo()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/ping.py", "same-key"))
            second = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/other.py", "same-key"))
            assert first.json()["task_id"] == second.json()["task_id"]
            finished = await _wait_terminal(client, first.json()["task_id"])
        assert finished["status"] == "completed"
        assert not (codebase / "src" / "other.py").exists()
        branch = finished["result"]["result_branch"]
        show = subprocess.run(
            ["git", "-C", str(codebase), "show", f"{branch}:src/other.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert show.returncode != 0
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_share_branches(task_db: Path):
    codebase = _copy_toy_repo()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.post("/api/v1/tasks/", json=_payload(codebase, "src/one.py", "one")),
                client.post("/api/v1/tasks/", json=_payload(codebase, "src/two.py", "two")),
            )
            assert first.json()["task_id"] != second.json()["task_id"]
            done_one, done_two = await asyncio.gather(
                _wait_terminal(client, first.json()["task_id"]),
                _wait_terminal(client, second.json()["task_id"]),
            )
        assert done_one["result"]["result_branch"] != done_two["result"]["result_branch"]
        assert 'return "src/one.py"' in done_one["result"]["code_diff"]
        assert 'return "src/two.py"' in done_two["result"]["code_diff"]
        assert not (codebase / "src" / "one.py").exists()
        assert not (codebase / "src" / "two.py").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


def test_two_processes_share_one_idempotency_key_and_one_claim(tmp_path: Path):
    database = tmp_path / "tasks.sqlite"
    left = TaskManager()
    right = TaskManager()
    left.bind(str(database))
    right.bind(str(database))
    first = left.create_task("once", str(tmp_path), idempotency_key="shared")
    again = right.create_task("twice", str(tmp_path), idempotency_key="shared")
    assert again.task_id == first.task_id
    assert left.claim_execution(first.task_id) is True
    assert right.claim_execution(first.task_id) is False
    left.use_memory()
    right.use_memory()


def test_restart_marks_running_tasks_interrupted_and_keeps_results(tmp_path: Path):
    database = tmp_path / "tasks.sqlite"
    first = TaskManager()
    first.bind(str(database))
    running = first.create_task("still going", str(tmp_path))
    first.update_status(running.task_id, TaskStatus.RUNNING)
    done = first.create_task("finished", str(tmp_path))
    first.set_result(done.task_id, {"result_branch": "agentforge/done", "code_diff": "diff"})

    restarted = TaskManager()
    interrupted = restarted.bind(str(database))
    assert interrupted == 1
    loaded_running = restarted.get_task(running.task_id)
    assert loaded_running is not None
    assert loaded_running.status == TaskStatus.INTERRUPTED
    assert loaded_running.error is not None
    assert "not resumed" in loaded_running.error
    loaded_done = restarted.get_task(done.task_id)
    assert loaded_done is not None
    assert loaded_done.status == TaskStatus.COMPLETED
    assert loaded_done.result == {"result_branch": "agentforge/done", "code_diff": "diff"}
    first.use_memory()
    restarted.use_memory()
