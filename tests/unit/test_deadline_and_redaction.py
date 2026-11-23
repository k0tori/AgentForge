"""Deadline and redaction. Git and threads are real; the model call is an async hang, not a paid API.

A stuck thread is left alive on purpose: asyncio cancellation does not stop it.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.api.task_manager import TaskEvent, TaskManager
from src.config import settings
from src.harness.deadline import DeadlineExceededError, bind_deadline, reset_deadline
from src.harness.workspace import SprintWorkspace
from src.llm.client import LLMClient
from src.logging_config import RedactFormatter, setup_logging
from src.workflow.graph import run_task

SECRET = "sk-injected-secret-value"


def test_failed_task_cannot_be_rewritten_as_success(tmp_path: Path):
    manager = TaskManager()
    manager.bind(str(tmp_path / "tasks.sqlite"))
    task = manager.create_task("running", str(tmp_path))
    manager.set_error(
        task.task_id,
        f"boom {SECRET}",
        code="execution_timeout",
        result={"note": SECRET, "trace": {"thought": SECRET}},
    )
    manager.set_result(task.task_id, {"final_verdict": "PASS", "note": SECRET})
    saved = manager.get_task(task.task_id)
    assert saved is not None
    assert saved.status.value == "failed"
    assert saved.result.get("final_verdict") != "PASS"
    blob = str(saved.error) + str(saved.result)
    assert SECRET not in blob
    manager.use_memory()


@pytest.mark.asyncio
async def test_streamed_event_and_log_drop_the_fake_secret(tmp_path: Path, capsys):
    manager = TaskManager()
    manager.bind(str(tmp_path / "tasks.sqlite"))
    task = manager.create_task("stream", str(tmp_path))
    await manager.emit_event(task.task_id, TaskEvent(event="note", data={"error": SECRET}))
    events = []
    async for event in manager.event_stream(task.task_id):
        events.append(event)
        break
    assert SECRET not in str(events)
    setup_logging()
    logging.getLogger("redaction-test").error("model said %s", SECRET)
    captured = capsys.readouterr().out
    assert SECRET not in captured
    assert "[redacted]" in captured
    manager.use_memory()


def test_log_formatter_redacts_without_setup(capsys):
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "header %s", (SECRET,), None)
    rendered = RedactFormatter("%(message)s").format(record)
    assert SECRET not in rendered


def test_in_flight_git_command_finishes_and_later_writes_do_not(tmp_path: Path, monkeypatch):
    codebase = tmp_path / "repo"
    codebase.mkdir()
    (codebase / "README.md").write_text("base\n", encoding="utf-8")
    workspace = SprintWorkspace(str(codebase), "slow-git", 1)
    root = Path(workspace.create())
    (root / "added.py").write_text("x = 1\n", encoding="utf-8")
    import src.harness.workspace as workspace_module

    real_git = workspace_module._git
    calls: list[tuple[str, ...]] = []

    def wrapped(cwd: Path, *args: str, check: bool = True):
        calls.append(args)
        if len(calls) == 1:
            time.sleep(0.35)
        return real_git(cwd, *args, check=check)

    monkeypatch.setattr(workspace_module, "_git", wrapped)
    token = bind_deadline(0.1)
    try:
        with pytest.raises(DeadlineExceededError):
            workspace.publish()
    finally:
        reset_deadline(token)
    assert calls, "the in-flight git command did not run"
    assert not any(command[:1] == ("commit",) for command in calls[1:])
    assert not any(command[:2] == ("worktree", "add") for command in calls[1:])
    branches = subprocess.run(
        ["git", "-C", str(codebase), "branch", "--list", "agentforge/*"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "agentforge/" not in branches.stdout


@pytest.mark.asyncio
async def test_blocking_publish_after_deadline_is_not_success(tmp_path: Path, monkeypatch):
    codebase = tmp_path / "repo"
    codebase.mkdir()
    (codebase / "README.md").write_text("base\n", encoding="utf-8")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setattr(settings, "SANDBOX_MODE", "host")
    monkeypatch.setattr(settings, "TIMEOUT_SECONDS", 0.05)

    class _Graph:
        async def ainvoke(self, state):
            time.sleep(0.2)
            workspace = SprintWorkspace(str(codebase), state["task_id"], 1)
            root = Path(workspace.create())
            (root / "late.py").write_text("y = 2\n", encoding="utf-8")
            workspace.publish()
            return {**state, "final_verdict": "PASS", "result_branch": "agentforge/late", "note": SECRET}

    async def degraded(_path: str):
        return [{"code": "retrieval_unavailable", "message": "Retrieval storage is unavailable."}]

    monkeypatch.setattr("src.workflow.preflight.assess_retrieval", degraded)
    monkeypatch.setattr("src.workflow.graph.get_compiled_graph", lambda: _Graph())
    state = await run_task("edit the repo", str(codebase), "blocked-publish")
    assert state["error_code"] == "execution_timeout"
    assert state["final_verdict"] == "FAIL"
    assert state["degradations"][0]["code"] == "retrieval_unavailable"
    assert SECRET not in str(state)
    listed = subprocess.run(
        ["git", "-C", str(codebase), "branch", "--list", "agentforge/*"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "agentforge/" not in listed.stdout


@pytest.mark.asyncio
async def test_model_await_is_cancelled_and_a_thread_is_not(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 0.1)
    client = LLMClient()
    cancelled = asyncio.Event()
    continued = False

    async def hang(_self, _func, *_args, **_kwargs):
        nonlocal continued
        try:
            await asyncio.Event().wait()
            continued = True
        finally:
            cancelled.set()

    monkeypatch.setattr(LLMClient, "_retry_with_backoff", hang)
    thread_done = threading.Event()

    def block():
        time.sleep(0.6)
        thread_done.set()

    thread = threading.Thread(target=block)
    thread.start()
    with pytest.raises(Exception) as exc:
        await client.chat([])
    assert exc.value.code == "model_timeout"
    assert cancelled.is_set()
    assert continued is False
    assert thread.is_alive()
    thread.join(timeout=2)
    assert thread_done.is_set()
    assert SECRET not in str(exc.value)


def test_cli_output_redacts_a_fake_secret(monkeypatch, capsys):
    from src.cli import main

    async def fake_run(*_args, **_kwargs):
        return {
            "task_id": "cli-secret",
            "error": f"provider returned {SECRET}",
            "error_code": "model_failed",
            "final_verdict": "FAIL",
            "degradations": [{"code": "retrieval_unavailable", "message": "off"}],
        }

    monkeypatch.setattr("src.cli.run_task", fake_run)
    with pytest.raises(SystemExit) as exc:
        main(["do not print this prompt", "--codebase", "toy-repo"])
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert SECRET not in output
    assert "do not print this prompt" not in output
    assert "retrieval_unavailable" in output
