"""Two operating-system processes sharing one task database and one git repo."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "support" / "m1_proc.py"


class _Proc:
    def __init__(self, args: list[str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(HELPER), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def read(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            error = self.proc.stderr.read() if self.proc.stderr is not None else ""
            raise AssertionError(f"helper exited: {error}")
        return json.loads(line)

    def call(self, payload: dict) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        return self.read()

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.call({"op": "stop"})
            except AssertionError:
                self.proc.kill()
        self.proc.wait(timeout=30)


def test_second_process_does_not_interrupt_a_live_owner_and_reads_current_status(tmp_path: Path):
    database = tmp_path / "tasks.sqlite"
    owner = _Proc(["owner", str(database)])
    reader = None
    try:
        ready = owner.read()
        assert ready["owner"] is True
        assert ready["interrupted"] == 0
        created = owner.call({"op": "create", "intent": "live", "path": str(tmp_path), "running": True})
        assert created["status"] == "running"
        reader = _Proc(["reader", str(database)])
        opened = reader.read()
        assert opened["owner"] is False
        seen = reader.call({"op": "get", "task_id": created["task_id"]})
        assert seen["status"] == "running"
        assert seen["error"] is None
        owner.call({
            "op": "complete",
            "task_id": created["task_id"],
            "result": {"result_branch": "agentforge/live", "acceptance": "not_run", "demo_completed": False},
        })
        fresh = reader.call({"op": "get", "task_id": created["task_id"]})
        assert fresh["status"] == "completed"
        assert fresh["result"]["result_branch"] == "agentforge/live"
    finally:
        if reader is not None:
            reader.close()
        owner.close()


def test_owner_exit_interrupts_only_after_the_lock_is_released(tmp_path: Path):
    database = tmp_path / "tasks.sqlite"
    owner = _Proc(["owner", str(database)])
    ready = owner.read()
    assert ready["interrupted"] == 0
    created = owner.call({"op": "create", "intent": "die", "path": str(tmp_path), "running": True})
    owner.close()

    restarted = _Proc(["owner", str(database)])
    try:
        again = restarted.read()
        assert again["owner"] is True
        assert again["interrupted"] == 1
        seen = restarted.call({"op": "get", "task_id": created["task_id"]})
        assert seen["status"] == "interrupted"
        assert "not resumed" in seen["error"]
        assert "not an accepted result" in seen["error"]
    finally:
        restarted.close()


def test_publish_lock_serializes_two_processes(tmp_path: Path):
    codebase = tmp_path / "repo"
    codebase.mkdir()
    (codebase / "README.md").write_text("base\n", encoding="utf-8")
    one = tmp_path / "one.py"
    two = tmp_path / "two.py"
    one.write_text('def marker() -> str:\n    return "one"\n', encoding="utf-8")
    two.write_text('def marker() -> str:\n    return "two"\n', encoding="utf-8")
    first = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "publish",
            str(tmp_path / "unused.sqlite"),
            str(codebase),
            "task-one",
            "src/one.py",
            str(one),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "publish",
            str(tmp_path / "unused.sqlite"),
            str(codebase),
            "task-two",
            "src/two.py",
            str(two),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out_one, err_one = first.communicate(timeout=60)
    out_two, err_two = second.communicate(timeout=60)
    assert first.returncode == 0, err_one
    assert second.returncode == 0, err_two
    branches = {json.loads(out_one)["branch"], json.loads(out_two)["branch"]}
    assert branches == {"agentforge/task-one", "agentforge/task-two"}
    show_one = subprocess.check_output(
        ["git", "-C", str(codebase), "show", "agentforge/task-one:src/one.py"],
        text=True,
    )
    show_two = subprocess.check_output(
        ["git", "-C", str(codebase), "show", "agentforge/task-two:src/two.py"],
        text=True,
    )
    assert 'return "one"' in show_one
    assert 'return "two"' in show_two
    assert not (codebase / "src" / "one.py").exists()
    assert not (codebase / "src" / "two.py").exists()
    assert (codebase / "README.md").read_text(encoding="utf-8") == "base\n"
