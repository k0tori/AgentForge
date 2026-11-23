"""Apply one reviewed file change and publish a branch. No LLM call.

This is a development and acceptance path. It does not measure model quality.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from src.harness.workspace import SprintWorkspace


def _safe_relative_py(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path.endswith(".py"):
        raise ValueError("path must be a relative .py file inside the codebase")
    return candidate


async def run_deterministic_change(
    *,
    codebase_path: str,
    task_id: str,
    change: dict,
) -> dict:
    """Write one file in a sprint workspace and commit it on a new branch."""
    relative = _safe_relative_py(str(change.get("path", "")))
    content = change.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    if len(content) > 100_000:
        raise ValueError("content is too large")

    workspace = SprintWorkspace(codebase_path, task_id, 1)
    workspace_path = Path(workspace.create(force_reseed=True))
    target = (workspace_path / relative).resolve()
    target.relative_to(workspace_path.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    original = Path(codebase_path) / relative
    old = original.read_text(encoding="utf-8") if original.is_file() else ""
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{relative.as_posix()}",
            tofile=f"b/{relative.as_posix()}",
        )
    )
    branch = workspace.publish()
    workspace.discard()
    return {
        "final_verdict": "PASS",
        "executor": "deterministic",
        "note": "This run did not call a model. It does not validate model quality.",
        "code_diff": diff,
        "result_branch": branch,
        "sprint_contract": [],
        "execution_trace_summary": 0,
        "cost_breakdown": {},
        "trace": None,
    }
