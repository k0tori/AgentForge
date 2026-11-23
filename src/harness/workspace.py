"""Sprint workspace lifecycle management.

Provides isolated, seeded working directories for each Sprint,
with merge-on-pass and discard-on-fail semantics.

This replaces the previous approach where Generator created an empty
tempdir (no seed) and Evaluator tested the original codebase_path
(not the Sprint workspace), making computational sensors meaningless.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.harness.deadline import DeadlineExceededError, deadline_exceeded

logger = logging.getLogger(__name__)
_GIT_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")


def git_subprocess_env() -> dict[str, str]:
    """Environment for git. Host secrets and the rest of the process env stay out."""
    env = {key: os.environ[key] for key in _GIT_ENV_KEYS if key in os.environ}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@contextmanager
def _publish_lock(path: Path) -> Iterator[None]:
    """Exclusive lock across processes. A threading.Lock would not do this."""
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "agentforge-publish-locks"
    directory.mkdir(parents=True, exist_ok=True)
    handle = os.open(directory / digest, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)

# Patterns excluded when seeding or merging the workspace
IGNORE_PATTERNS = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", "node_modules")


class SprintWorkspace:
    """Sprint 的隔离工作目录：负责 seed → 暴露路径 → merge/discard。"""

    def __init__(self, codebase_path: str, task_id: str, sprint: int) -> None:
        self.codebase_path = Path(codebase_path)
        self.task_id = task_id
        self.sprint = sprint
        self.path = Path(tempfile.gettempdir()) / f"agentforge_sprint_{task_id}_{sprint}"

    def create(self, *, force_reseed: bool = True, preserve_symlinks: bool = False) -> str:
        """Seed: 把现有代码完整拷进 sprint workspace，Generator 在这个基础上改。

        Args:
            force_reseed: True (默认) 则清除已有 workspace 重新 seed；
                          False 则保留已有内容（retry 时复用上次的代码）。
        """
        if self.path.exists():
            if force_reseed:
                shutil.rmtree(self.path)
            else:
                logger.info("Sprint workspace preserved for retry: %s", self.path)
                return str(self.path)
        shutil.copytree(
            self.codebase_path,
            self.path,
            ignore=IGNORE_PATTERNS,
            symlinks=preserve_symlinks,
        )
        logger.info("Sprint workspace seeded: %s -> %s", self.codebase_path, self.path)
        return str(self.path)

    def merge(self) -> None:
        """Copy the workspace onto the codebase working tree, then delete the temp dir."""
        shutil.copytree(self.path, self.codebase_path, dirs_exist_ok=True, ignore=IGNORE_PATTERNS)
        logger.info("Sprint workspace merged: %s -> %s", self.path, self.codebase_path)
        self.discard()

    def publish(self) -> str:
        """Commit the workspace onto a new branch. Leave the working tree as it is.

        Returns the branch name. The temp workspace is left in place so the caller
        can discard it after reading the diff.
        """
        if deadline_exceeded():
            raise DeadlineExceededError("PGE deadline exceeded before publish")
        if not self.path.is_dir():
            raise FileNotFoundError(f"Sprint workspace does not exist: {self.path}")
        branch = _branch_name(self.task_id)
        with _publish_lock(self.codebase_path):
            if deadline_exceeded():
                raise DeadlineExceededError("PGE deadline exceeded before publish")
            branch_name = self._publish_locked(branch)
        if deadline_exceeded():
            raise DeadlineExceededError("PGE deadline exceeded during publish")
        return branch_name

    def _publish_locked(self, branch: str) -> str:
        self._ensure_repo()
        worktree = Path(tempfile.mkdtemp(prefix="agentforge_publish_"))
        try:
            _git(self.codebase_path, "worktree", "add", "-B", branch, str(worktree), "HEAD")
            _sync_tree(self.path, worktree)
            _git(worktree, "add", "-A")
            status = _git(worktree, "status", "--porcelain", check=False)
            if status.stdout.strip():
                _git(worktree, "commit", "-m", f"agentforge: apply task {self.task_id}")
            logger.info("Published sprint workspace to branch %s", branch)
            return branch
        finally:
            _git(self.codebase_path, "worktree", "remove", "--force", str(worktree), check=False)
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)

    def _ensure_repo(self) -> None:
        """Make codebase_path its own git root so a parent repo is not modified."""
        if not (self.codebase_path / ".git").exists():
            _git(self.codebase_path, "init")
        if _git(self.codebase_path, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
            _git(self.codebase_path, "add", "-A")
            _git(self.codebase_path, "commit", "--allow-empty", "-m", "agentforge baseline")

    def discard(self) -> None:
        """FAIL 或合并完成后：丢弃临时目录。"""
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
            logger.info("Sprint workspace discarded: %s", self.path)


_SKIP_NAMES = frozenset({".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"})


def published_branch_name(task_id: str) -> str:
    """Branch name publish() would use for this task. It is not an acceptance."""
    return _branch_name(task_id)


def _branch_name(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-") or "task"
    return f"agentforge/{safe}"


def _writes_git_state(args: tuple[str, ...]) -> bool:
    if not args:
        return False
    if args[0] in {"commit", "add", "init"}:
        return True
    return args[0] == "worktree" and len(args) > 1 and args[1] == "add"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if _writes_git_state(args) and deadline_exceeded():
        raise DeadlineExceededError("PGE deadline exceeded before git")
    command = [
        "git",
        "-c", "user.email=agentforge@local",
        "-c", "user.name=AgentForge",
        "-c", "core.hooksPath=/dev/null",
        "-c", "init.defaultBranch=main",
        *args,
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=git_subprocess_env(),
    )


def _sync_tree(source: Path, destination: Path) -> None:
    """Make destination match source, without touching destination/.git."""
    kept: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_NAMES and not name.startswith(".")]
        for filename in filenames:
            if filename.endswith(".pyc"):
                continue
            full = Path(dirpath) / filename
            relative = full.relative_to(source).as_posix()
            kept.add(relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, target)
    for dirpath, dirnames, filenames in os.walk(destination):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_NAMES]
        for filename in filenames:
            if filename == ".git":
                continue
            full = Path(dirpath) / filename
            relative = full.relative_to(destination).as_posix()
            if relative not in kept:
                full.unlink(missing_ok=True)
