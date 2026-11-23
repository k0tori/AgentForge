"""SQLite task records. One file, no extra service, survives process restart."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from src.api.task_manager import TaskEvent, TaskState, TaskStatus


class SqliteTaskStore:
    """Persist task rows. Callers serialize access with the store lock."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
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
                events_json TEXT NOT NULL,
                idempotency_key TEXT UNIQUE
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def load_all(self) -> list[TaskState]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT task_id, status, intent, codebase_path, created_at, completed_at,
                       result_json, error, events_json, idempotency_key
                FROM tasks
                """
            ).fetchall()
        return [_row_to_state(row) for row in rows]

    def upsert(self, task: TaskState, idempotency_key: str | None) -> None:
        payload = (
            str(task.task_id),
            task.status.value,
            task.intent,
            task.codebase_path,
            task.created_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at else None,
            json.dumps(task.result) if task.result is not None else None,
            task.error,
            json.dumps([event.model_dump(mode="json") for event in task.events]),
            idempotency_key,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, status, intent, codebase_path, created_at, completed_at,
                    result_json, error, events_json, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    completed_at=excluded.completed_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    events_json=excluded.events_json
                """,
                payload,
            )
            self._conn.commit()

    def find_id_by_idempotency_key(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id FROM tasks WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row[0])

    def get(self, task_id: str) -> TaskState | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT task_id, status, intent, codebase_path, created_at, completed_at,
                       result_json, error, events_json, idempotency_key
                FROM tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        return None if row is None else _row_to_state(row)

    def try_claim(self, task_id: str) -> bool:
        """Move pending -> running once. A second caller in any process gets False."""
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
    events = [TaskEvent.model_validate(item) for item in json.loads(row[8])]
    return TaskState(
        task_id=row[0],
        status=TaskStatus(row[1]),
        intent=row[2],
        codebase_path=row[3],
        created_at=_parse_dt(row[4]),
        completed_at=_parse_dt(row[5]),
        result=json.loads(row[6]) if row[6] else None,
        error=row[7],
        events=events,
        idempotency_key=row[9],
    )
