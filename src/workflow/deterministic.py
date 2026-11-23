"""Write one file and publish a branch. This is a demo, not a PGE acceptance.

It does not call a model, does not run pytest or ruff, and must not report PASS.
"""
from __future__ import annotations

import difflib
import os
from collections.abc import Callable
from pathlib import Path

from src.harness.workspace import SprintWorkspace


def _safe_relative_py(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path.endswith(".py"):
        raise ValueError("path must be a relative .py file inside the codebase")
    if any(part in ("", ".", "..") for part in candidate.parts):
        raise ValueError("path must be a relative .py file inside the codebase")
    return candidate


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("refusing to follow a symlink")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            if (Path(dirpath) / name).is_symlink():
                raise ValueError("refusing to follow a symlink")


def _destination(workspace: Path, relative: Path) -> Path:
    current = workspace
    if current.is_symlink():
        raise ValueError("refusing to follow a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("refusing to follow a symlink")
    current.resolve().relative_to(workspace.resolve())
    return current


async def run_deterministic_change(
    *,
    codebase_path: str,
    task_id: str,
    change: dict,
    on_published: Callable[[str], None] | None = None,
) -> dict:
    """Write one file in a sprint workspace and commit it on a new branch.

    The returned verdict is not PASS. Callers have to show demo_completed separately.
    """
    relative = _safe_relative_py(str(change.get("path", "")))
    content = change.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    if len(content) > 100_000:
        raise ValueError("content is too large")

    workspace = SprintWorkspace(codebase_path, task_id, 1)
    try:
        workspace_path = Path(workspace.create(force_reseed=True, preserve_symlinks=True))
        _reject_symlinks(workspace_path)
        target = _destination(workspace_path, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("refusing to follow a symlink")
        target.write_text(content, encoding="utf-8")
        if target.is_symlink():
            raise ValueError("refusing to follow a symlink")
        target.resolve().relative_to(workspace_path.resolve())

        original = Path(codebase_path) / relative
        if original.is_symlink():
            raise ValueError("refusing to follow a symlink")
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
        if on_published is not None:
            on_published(branch)
    finally:
        workspace.discard()
    return {
        "final_verdict": None,
        "acceptance": "not_run",
        "demo_completed": True,
        "executor": "deterministic",
        "note": (
            "Demo completion only. No sensor ran and this is not a PGE acceptance PASS. "
            "It does not validate model quality."
        ),
        "code_diff": diff,
        "result_branch": branch,
        "sprint_contract": [],
        "execution_trace_summary": 0,
        "cost_breakdown": {},
        "trace": None,
    }
