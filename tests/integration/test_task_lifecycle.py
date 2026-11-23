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


def _copy_toy_repo(*, commit: bool = True) -> Path:
    """Copy toy-repo. Publish needs a committed repo, so the default commits the copy."""
    destination = Path.cwd() / ".agentforge" / "it" / uuid.uuid4().hex
    shutil.copytree(Path.cwd() / "toy-repo", destination, ignore=shutil.ignore_patterns(".git"))
    if commit:
        subprocess.run(["git", "init", "-b", "main"], cwd=destination, check=True, capture_output=True)
        git = ["git", "-c", "user.email=test@local", "-c", "user.name=test", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "add", "-A"], cwd=destination, check=True, capture_output=True)
        subprocess.run([*git, "commit", "-m", "demo baseline"], cwd=destination, check=True, capture_output=True)
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
    monkeypatch.setattr("src.config.settings.DETERMINISTIC_EXECUTOR", True)
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
        assert finished["status"] == "completed", finished.get("error")
        assert finished["error"] is None
        result = finished["result"]
        assert result["executor"] == "deterministic"
        assert result["final_verdict"] is None
        assert result["acceptance"] == "not_run"
        assert result["demo_completed"] is True
        assert "not a PGE acceptance PASS" in result["note"]
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
        assert finished["result"]["demo_completed"] is False
        assert not finished["result"].get("result_branch")
        assert not (codebase.parent / "escape.py").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_same_idempotency_key_and_body_reuses_one_task(task_db: Path):
    codebase = _copy_toy_repo()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = _payload(codebase, "src/ping.py", "same-key")
            first = await client.post("/api/v1/tasks/", json=payload)
            second = await client.post("/api/v1/tasks/", json=payload)
            assert first.status_code == 202
            assert second.status_code == 202
            assert first.json()["task_id"] == second.json()["task_id"]
            finished = await _wait_terminal(client, first.json()["task_id"])
        assert finished["status"] == "completed", finished.get("error")
        assert finished["result"]["demo_completed"] is True
        assert finished["result"]["acceptance"] == "not_run"
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_body_conflicts(task_db: Path):
    codebase = _copy_toy_repo()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/ping.py", "same-key"))
            second = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/other.py", "same-key"))
            assert second.status_code == 409
            assert first.json()["task_id"] in second.json()["detail"]
            finished = await _wait_terminal(client, first.json()["task_id"])
        assert finished["status"] == "completed", finished.get("error")
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
        assert done_one["status"] == "completed", done_one.get("error")
        assert done_two["status"] == "completed", done_two.get("error")
        assert done_one["result"]["result_branch"] != done_two["result"]["result_branch"]
        assert 'return "src/one.py"' in done_one["result"]["code_diff"]
        assert 'return "src/two.py"' in done_two["result"]["code_diff"]
        assert not (codebase / "src" / "one.py").exists()
        assert not (codebase / "src" / "two.py").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


def test_reader_can_see_status_but_cannot_mark_success(tmp_path: Path):
    from src.api.task_manager import WorkerBusyError

    database = tmp_path / "tasks.sqlite"
    owner = TaskManager()
    owner.bind(str(database))
    running = owner.create_task("still going", str(tmp_path))
    owner.update_status(running.task_id, TaskStatus.RUNNING)
    reader = TaskManager()
    reader.bind(str(database), owner=False)
    assert reader.get_task(running.task_id).status == TaskStatus.RUNNING
    with pytest.raises(WorkerBusyError):
        reader.set_result(running.task_id, {"final_verdict": "PASS", "demo_completed": True})
    assert owner.get_task(running.task_id).status == TaskStatus.RUNNING
    owner.use_memory()
    reader.use_memory()


def test_live_owner_keeps_running_task_when_another_manager_cannot_bind(tmp_path: Path):
    from src.api.task_manager import WorkerBusyError

    database = tmp_path / "tasks.sqlite"
    first = TaskManager()
    first.bind(str(database))
    running = first.create_task("still going", str(tmp_path))
    first.update_status(running.task_id, TaskStatus.RUNNING)
    second = TaskManager()
    with pytest.raises(WorkerBusyError):
        second.bind(str(database))
    assert first.get_task(running.task_id).status == TaskStatus.RUNNING
    first.use_memory()
    second.use_memory()


def test_restart_marks_running_tasks_interrupted_and_keeps_results(tmp_path: Path):
    database = tmp_path / "tasks.sqlite"
    first = TaskManager()
    first.bind(str(database))
    running = first.create_task("still going", str(tmp_path))
    first.update_status(running.task_id, TaskStatus.RUNNING)
    first.record_branch(running.task_id, "agentforge/still")
    done = first.create_task("finished", str(tmp_path))
    first.set_result(done.task_id, {"result_branch": "agentforge/done", "code_diff": "diff"})
    first.use_memory()

    restarted = TaskManager()
    interrupted = restarted.bind(str(database))
    assert interrupted == 1
    loaded_running = restarted.get_task(running.task_id)
    assert loaded_running is not None
    assert loaded_running.status == TaskStatus.INTERRUPTED
    assert loaded_running.error is not None
    assert "not resumed" in loaded_running.error
    assert "not an accepted result" in loaded_running.error
    assert loaded_running.result is not None
    assert loaded_running.result["result_branch"] == "agentforge/still"
    assert loaded_running.result["demo_completed"] is False
    loaded_done = restarted.get_task(done.task_id)
    assert loaded_done is not None
    assert loaded_done.status == TaskStatus.COMPLETED
    assert loaded_done.result == {"result_branch": "agentforge/done", "code_diff": "diff"}
    restarted.set_result(running.task_id, {"final_verdict": "PASS", "demo_completed": True})
    still = restarted.get_task(running.task_id)
    assert still is not None
    assert still.status == TaskStatus.INTERRUPTED
    assert still.result["demo_completed"] is False
    restarted.use_memory()


@pytest.mark.asyncio
async def test_deterministic_executor_is_disabled_unless_enabled(task_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.config.settings.DETERMINISTIC_EXECUTOR", False)
    codebase = _copy_toy_repo(commit=False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/ping.py"))
        assert created.status_code == 403
        assert not (codebase / ".git").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)


@pytest.mark.asyncio
async def test_deterministic_codebase_must_stay_under_agentforge(task_db: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/tasks/",
            json=_payload(Path.cwd() / "toy-repo", "src/ping.py"),
        )
    assert created.status_code == 400
    assert ".agentforge" in created.json()["detail"]


@pytest.mark.asyncio
async def test_deterministic_symlink_is_not_published(task_db: Path):
    codebase = _copy_toy_repo(commit=False)
    secret = codebase.parent / "secret.txt"
    secret.write_text("SECRET_TOKEN")
    (codebase / "src" / "leak.py").symlink_to(secret)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/tasks/", json=_payload(codebase, "src/ping.py"))
            finished = await _wait_terminal(client, created.json()["task_id"])
        assert finished["status"] == "failed"
        assert "symlink" in finished["error"]
        assert finished["result"] is None or finished["result"].get("demo_completed") is not True
        assert secret.read_text(encoding="utf-8") == "SECRET_TOKEN"
        assert not (codebase / ".git").exists()
    finally:
        shutil.rmtree(codebase, ignore_errors=True)
        secret.unlink(missing_ok=True)
