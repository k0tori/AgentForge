"""Subprocess probe for the task database. Pytest does not collect this module."""
from __future__ import annotations

import json
import sys
import traceback
import uuid
from pathlib import Path


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _task_payload(task) -> dict:
    if task is None:
        return {"status": None, "error": None, "result": None}
    return {"status": task.status.value, "error": task.error, "result": task.result}


def _serve(manager, *, stop_releases: bool) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        op = request["op"]
        if op == "stop":
            if stop_releases:
                manager.use_memory()
            _emit({"ok": True})
            return
        if op == "create":
            task = manager.create_task(
                request["intent"],
                request["path"],
                idempotency_key=request.get("key"),
                request_hash=request.get("hash"),
            )
            if request.get("running"):
                from src.api.task_manager import TaskStatus

                manager.update_status(task.task_id, TaskStatus.RUNNING)
            _emit({"task_id": str(task.task_id), "status": manager.get_task(task.task_id).status.value})
        elif op == "get":
            _emit(_task_payload(manager.get_task(uuid.UUID(request["task_id"]))))
        elif op == "complete":
            manager.set_result(uuid.UUID(request["task_id"]), request["result"])
            _emit({"ok": True})
        else:
            raise ValueError(f"unknown op {op}")


def main() -> None:
    command = sys.argv[1]
    database = sys.argv[2]
    if command == "publish":
        from src.harness.workspace import SprintWorkspace

        codebase, task_id, relative, content_file = sys.argv[3:7]
        workspace = SprintWorkspace(codebase, task_id, 1)
        root = Path(workspace.create(force_reseed=True))
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(content_file).read_text(encoding="utf-8"), encoding="utf-8")
        branch = workspace.publish()
        workspace.discard()
        _emit({"branch": branch})
        return

    from src.api.task_manager import TaskManager, WorkerBusyError

    manager = TaskManager()
    if command == "owner":
        interrupted = manager.bind(database)
        _emit({"ready": True, "owner": True, "interrupted": interrupted})
        _serve(manager, stop_releases=True)
        return
    if command == "reader":
        try:
            manager.bind(database)
        except WorkerBusyError:
            manager.bind(database, owner=False)
            _emit({"ready": True, "owner": False})
            _serve(manager, stop_releases=True)
            return
        _emit({"ready": True, "owner": True, "error": "lock was not held"})
        return
    raise ValueError(command)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
