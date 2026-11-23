"""SQLite task records. One file, no extra service, survives process restart."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from src.api.task_manager import TaskEvent, TaskState, TaskStatus

_COLUMNS = (
    "task_id, status, intent, codebase_path, created_at, completed_at, "
    "result_json, error, error_code, events_json, idempotency_key, request_hash"
)


class SqliteTaskStore:
    """Persist task rows. The owner lock is an OS flock held until this process exits."""

    def __init__(self, path: str) -> None:
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._owner_fd: int | None = None
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                intent TEXT NOT NULL,
                codebase_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                result_json TEXT,
                error TEXT,
                error_code TEXT,
                events_json TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                request_hash TEXT
            )
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        if "request_hash" not in columns:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN request_hash TEXT")
        if "error_code" not in columns:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN error_code TEXT")
        self._conn.commit()

    def acquire_owner(self) -> bool:
        """Take the single-writer lock. False means another live process holds it."""
        if self._owner_fd is not None:
            return True
        lock_path = Path(self._path + ".owner.lock")
        handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(handle)
            return False
        os.ftruncate(handle, 0)
        os.write(handle, str(os.getpid()).encode())
        self._owner_fd = handle
        return True

    def close(self) -> None:
        if self._owner_fd is not None:
            os.close(self._owner_fd)
            self._owner_fd = None
        with self._lock:
            self._conn.close()

    def load_all(self) -> list[TaskState]:
        with self._lock:
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM tasks").fetchall()
        return [_row_to_state(row) for row in rows]

    def upsert(self, task: TaskState) -> None:
        payload = (
            str(task.task_id),
            task.status.value,
            task.intent,
            task.codebase_path,
            task.created_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at else None,
            json.dumps(task.result) if task.result is not None else None,
            task.error,
            task.error_code,
            json.dumps([event.model_dump(mode="json") for event in task.events]),
            task.idempotency_key,
            task.request_hash,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, status, intent, codebase_path, created_at, completed_at,
                    result_json, error, error_code, events_json, idempotency_key, request_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    error_code=excluded.error_code,
                    events_json=excluded.events_json,
                    request_hash=excluded.request_hash
                """,
                payload,
            )
            self._conn.commit()

    def find_by_idempotency_key(self, key: str) -> tuple[str, str | None] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id, request_hash FROM tasks WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), row[1]

    def get(self, task_id: str) -> TaskState | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return None if row is None else _row_to_state(row)

    def try_claim(self, task_id: str) -> bool:
        """Move pending -> running once. A second caller in this process gets False."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ? AND status = ?",
                (TaskStatus.RUNNING.value, task_id, TaskStatus.PENDING.value),
            )
            self._conn.commit()
            return cursor.rowcount == 1


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _row_to_state(row: tuple) -> TaskState:
    events = [TaskEvent.model_validate(item) for item in json.loads(row[9])]
    return TaskState(
        task_id=row[0],
        status=TaskStatus(row[1]),
        intent=row[2],
        codebase_path=row[3],
        created_at=_parse_dt(row[4]),
        completed_at=_parse_dt(row[5]),
        result=json.loads(row[6]) if row[6] else None,
        error=row[7],
        error_code=row[8],
        events=events,
        idempotency_key=row[10],
        request_hash=row[11],
    )
