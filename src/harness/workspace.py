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
import json
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
from src.harness.publish_guard import (
    PublishRejectedError,
    assert_delta_is_publishable,
    assert_destination_is_safe,
    is_sensitive_path,
    scan_tree,
    symlink_escapes,
    tree_delta,
    write_regular_file,
)

logger = logging.getLogger(__name__)
_GIT_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")


def git_subprocess_env() -> dict[str, str]:
    """Environment for git. Host secrets and the rest of the process env stay out."""
    env = {key: os.environ[key] for key in _GIT_ENV_KEYS if key in os.environ}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = "AgentForge"
    env["GIT_AUTHOR_EMAIL"] = "agentforge@local"
    env["GIT_COMMITTER_NAME"] = "AgentForge"
    env["GIT_COMMITTER_EMAIL"] = "agentforge@local"
    return env


def _empty_hooks_dir() -> str:
    """A real directory with no hook scripts. /dev/null is not a directory on newer git."""
    path = Path(tempfile.gettempdir()) / "agentforge-empty-hooks"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


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
        self.residual_branch: str | None = None

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
            symlinks=True,
        )
        self._write_seed(scan_tree(self.path))
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
            try:
                branch_name = self._publish_locked(branch)
            except DeadlineExceededError as exc:
                if exc.branch is None and self.residual_branch:
                    raise DeadlineExceededError(str(exc), branch=self.residual_branch) from None
                raise
            except PublishRejectedError as exc:
                if exc.branch is None and self.residual_branch:
                    raise PublishRejectedError(str(exc), branch=self.residual_branch) from None
                raise
        if deadline_exceeded():
            raise DeadlineExceededError(
                "PGE deadline exceeded during publish",
                branch=branch_name,
            )
        return branch_name

    def _publish_locked(self, branch: str) -> str:
        self._ensure_repo()
        self._reject_executable_filters()
        seed = self._read_seed()
        current = scan_tree(self.path)
        added, modified, deleted = tree_delta(seed, current)
        assert_delta_is_publishable(self.path, current, added, modified, deleted)
        worktree = Path(tempfile.mkdtemp(prefix="agentforge_publish_"))
        try:
            _git(self.codebase_path, "worktree", "add", "-B", branch, str(worktree), "HEAD")
            self.residual_branch = branch
            head_symlinks = _head_symlink_paths(self.codebase_path)
            for relative in [*added, *modified, *deleted]:
                assert_destination_is_safe(worktree, head_symlinks, relative)
            for relative in [*added, *modified]:
                meta = current[relative]
                if meta["kind"] == "symlink":
                    raise PublishRejectedError(
                        f"Refusing to publish {relative}: symlink changes are not copied onto HEAD."
                    )
                write_regular_file(worktree, relative, (self.path / relative).read_bytes())
            if added or modified:
                _git(worktree, "add", "--", *added, *modified)
            for relative in deleted:
                _git(worktree, "rm", "--ignore-unmatch", "--", relative)
            status = _git(worktree, "status", "--porcelain", check=False)
            if status.stdout.strip():
                _git(worktree, "commit", "-m", f"agentforge: apply task {self.task_id}")
            self.residual_branch = branch
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
            for relative, meta in scan_tree(self.codebase_path).items():
                if is_sensitive_path(relative):
                    continue
                if meta["kind"] == "symlink" and symlink_escapes(self.codebase_path, relative, meta["target"]):
                    continue
                _git(self.codebase_path, "add", "--", relative)
            _git(self.codebase_path, "commit", "--allow-empty", "-m", "agentforge baseline")

    def _reject_executable_filters(self) -> None:
        """hooksPath does not stop clean/smudge filters. Refuse them before checkout."""
        config_file = self.codebase_path / ".git" / "config"
        config_text = config_file.read_text(encoding="utf-8", errors="replace") if config_file.is_file() else ""
        if re.search(r"(?im)^\[filter(\s|\])", config_text):
            raise PublishRejectedError(
                "Refusing to publish: .git/config defines a clean/smudge filter. "
                "core.hooksPath does not disable filters, so checkout and git add would run "
                "repository commands on the host. Remove the [filter] section and retry. "
                "Nothing was committed."
            )
        for attributes in self.codebase_path.rglob(".gitattributes"):
            if ".git" in attributes.parts:
                continue
            text = attributes.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(^|\s)filter=", text):
                raise PublishRejectedError(
                    "Refusing to publish: .gitattributes requests a git filter. "
                    "Publish does not run clean or smudge commands. Nothing was committed."
                )

    @property
    def _seed_path(self) -> Path:
        return Path(str(self.path) + ".seed.json")

    def _write_seed(self, manifest: dict[str, dict[str, str]]) -> None:
        self._seed_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def _read_seed(self) -> dict[str, dict[str, str]]:
        if not self._seed_path.is_file():
            raise PublishRejectedError(
                "Sprint seed manifest is missing. Refusing to guess which files the task changed."
            )
        loaded = json.loads(self._seed_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise PublishRejectedError("Sprint seed manifest is unreadable.")
        return loaded

    def discard(self) -> None:
        """FAIL 或合并完成后：丢弃临时目录。"""
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
            logger.info("Sprint workspace discarded: %s", self.path)
        self._seed_path.unlink(missing_ok=True)


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
        "-c", f"core.hooksPath={_empty_hooks_dir()}",
        "-c", "commit.gpgsign=false",
        "-c", "tag.gpgsign=false",
        "-c", "init.defaultBranch=main",
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=git_subprocess_env(),
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git failed").strip()
        raise PublishRejectedError(f"git {' '.join(args)} failed: {detail[:300]}")
    return completed


def _head_symlink_paths(repo: Path) -> set[str]:
    listed = _git(repo, "ls-tree", "-r", "HEAD", check=False)
    if listed.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in listed.stdout.splitlines():
        meta, _, path = line.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode == "120000" and path:
            paths.add(path)
    return paths


def _remove_published_path(worktree: Path, relative: str) -> None:
    current = worktree
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PublishRejectedError(f"Refusing to delete through symlink {relative}")
        if not current.exists():
            return
    if current.is_file():
        current.unlink()
