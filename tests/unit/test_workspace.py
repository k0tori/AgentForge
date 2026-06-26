"""Tests for SprintWorkspace lifecycle management."""

from __future__ import annotations

import os
from pathlib import Path

from src.harness.workspace import SprintWorkspace


def test_create_seeds_codebase(tmp_path: Path) -> None:
    """create() should copy existing codebase into sprint workspace."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "main.py").write_text("print('hello')")
    (codebase / "models").mkdir()
    (codebase / "models" / "user.py").write_text("class User: pass")

    ws = SprintWorkspace(str(codebase), "test-001", 1)
    workspace_path = ws.create()

    assert os.path.isdir(workspace_path)
    assert (Path(workspace_path) / "main.py").read_text() == "print('hello')"
    assert (Path(workspace_path) / "models" / "user.py").read_text() == "class User: pass"

    ws.discard()


def test_create_excludes_git_and_pycache(tmp_path: Path) -> None:
    """create() should exclude .git, __pycache__, etc."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "main.py").write_text("x = 1")
    (codebase / ".git").mkdir()
    (codebase / ".git" / "config").write_text("git config")
    (codebase / "__pycache__").mkdir()
    (codebase / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00")

    ws = SprintWorkspace(str(codebase), "test-002", 1)
    workspace_path = ws.create()

    assert not (Path(workspace_path) / ".git").exists()
    assert not (Path(workspace_path) / "__pycache__").exists()
    assert (Path(workspace_path) / "main.py").exists()

    ws.discard()


def test_merge_copies_back_to_codebase(tmp_path: Path) -> None:
    """merge() should copy workspace files back to codebase."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "existing.py").write_text("x = 1")

    ws = SprintWorkspace(str(codebase), "test-003", 1)
    workspace_path = ws.create()

    # Simulate Generator writing new files in workspace
    (Path(workspace_path) / "new_module.py").write_text("y = 2")
    (Path(workspace_path) / "existing.py").write_text("x = 10")  # modified

    ws.merge()

    # Verify changes landed in codebase
    assert (codebase / "new_module.py").read_text() == "y = 2"
    assert (codebase / "existing.py").read_text() == "x = 10"

    # Verify temp workspace was cleaned up
    assert not os.path.isdir(workspace_path)


def test_discard_removes_workspace(tmp_path: Path) -> None:
    """discard() should remove the sprint workspace directory."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "main.py").write_text("x = 1")

    ws = SprintWorkspace(str(codebase), "test-004", 1)
    workspace_path = ws.create()
    assert os.path.isdir(workspace_path)

    ws.discard()
    assert not os.path.isdir(workspace_path)


def test_discard_is_idempotent(tmp_path: Path) -> None:
    """discard() should not raise if workspace already removed."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()

    ws = SprintWorkspace(str(codebase), "test-005", 1)
    ws.create()
    ws.discard()
    ws.discard()  # should not raise


def test_create_cleans_existing_workspace(tmp_path: Path) -> None:
    """create(force_reseed=True) should remove and re-seed if workspace already exists."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "v1.py").write_text("version 1")

    ws = SprintWorkspace(str(codebase), "test-006", 1)

    # First create
    path1 = ws.create(force_reseed=True)
    assert (Path(path1) / "v1.py").read_text() == "version 1"

    # Modify codebase and create again with force_reseed
    (codebase / "v1.py").write_text("version 2")
    (codebase / "v2.py").write_text("new file")
    path2 = ws.create(force_reseed=True)

    assert path1 == path2  # same path
    assert (Path(path2) / "v1.py").read_text() == "version 2"
    assert (Path(path2) / "v2.py").read_text() == "new file"

    ws.discard()


def test_create_preserves_workspace_on_retry(tmp_path: Path) -> None:
    """create(force_reseed=False) should keep previous attempt's code."""
    codebase = tmp_path / "codebase"
    codebase.mkdir()
    (codebase / "main.py").write_text("original")

    ws = SprintWorkspace(str(codebase), "test-007", 1)

    # First create — seeds from codebase
    path1 = ws.create(force_reseed=True)
    assert (Path(path1) / "main.py").read_text() == "original"

    # Simulate Generator modifying workspace (not codebase)
    (Path(path1) / "main.py").write_text("generator attempt 1")
    (Path(path1) / "new_feature.py").write_text("attempt 1 code")

    # Retry with force_reseed=False — should keep Generator's work
    path2 = ws.create(force_reseed=False)
    assert path1 == path2
    assert (Path(path2) / "main.py").read_text() == "generator attempt 1"
    assert (Path(path2) / "new_feature.py").read_text() == "attempt 1 code"

    # Codebase itself should be untouched
    assert (codebase / "main.py").read_text() == "original"
    assert not (codebase / "new_feature.py").exists()

    ws.discard()
