"""Per-task retrieval scope.

The active codebase path is set for the duration of run_task so RAG tools
search the repository that task is editing.
"""
from __future__ import annotations

from contextvars import ContextVar

project_path_var: ContextVar[str | None] = ContextVar("agentforge_project_path", default=None)
