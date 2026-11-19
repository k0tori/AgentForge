"""One sandbox attempt for a generate or evaluate call.

Commands started while the session is active share an attempt id and are removed
when the attempt ends. The session does not mount the Docker socket.
"""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from src.harness.sandbox.runtime import cleanup_attempt

_session: ContextVar[SandboxSession | None] = ContextVar("agentforge_sandbox_session", default=None)


@dataclass
class SandboxSession:
    """Identity of the current generate or evaluate attempt."""

    workspace: str
    attempt_id: str
    role: str


def current_session() -> SandboxSession | None:
    return _session.get()


@contextmanager
def sandbox_session(workspace: str, *, task_id: str, role: str):
    """Bind subsequent pytest/ruff runs to this attempt, then delete its containers."""
    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id).strip("-") or "task"
    safe_role = re.sub(r"[^A-Za-z0-9_.-]+", "-", role).strip("-") or "run"
    attempt_id = f"{safe_role}-{safe_task}-{uuid.uuid4().hex[:8]}"[:63].strip("-")
    session = SandboxSession(workspace=workspace, attempt_id=attempt_id, role=safe_role)
    token = _session.set(session)
    try:
        yield session
    finally:
        _session.reset(token)
        cleanup_attempt(attempt_id)
