"""Sprint workspace lifecycle management.

Provides isolated, seeded working directories for each Sprint,
with merge-on-pass and discard-on-fail semantics.

This replaces the previous approach where Generator created an empty
tempdir (no seed) and Evaluator tested the original codebase_path
(not the Sprint workspace), making computational sensors meaningless.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Patterns excluded when seeding or merging the workspace
IGNORE_PATTERNS = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", "node_modules")


class SprintWorkspace:
    """Sprint 的隔离工作目录：负责 seed → 暴露路径 → merge/discard。"""

    def __init__(self, codebase_path: str, task_id: str, sprint: int) -> None:
        self.codebase_path = Path(codebase_path)
        self.path = Path(tempfile.gettempdir()) / f"agentforge_sprint_{task_id}_{sprint}"

    def create(self) -> str:
        """Seed: 把现有代码完整拷进 sprint workspace，Generator 在这个基础上改。"""
        if self.path.exists():
            shutil.rmtree(self.path)
        shutil.copytree(self.codebase_path, self.path, ignore=IGNORE_PATTERNS)
        logger.info("Sprint workspace seeded: %s -> %s", self.codebase_path, self.path)
        return str(self.path)

    def merge(self) -> None:
        """PASS 之后：把 workspace 覆盖回 codebase_path，再清理临时目录。"""
        shutil.copytree(self.path, self.codebase_path, dirs_exist_ok=True, ignore=IGNORE_PATTERNS)
        logger.info("Sprint workspace merged: %s -> %s", self.path, self.codebase_path)
        self.discard()

    def discard(self) -> None:
        """FAIL 或合并完成后：丢弃临时目录。"""
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
            logger.info("Sprint workspace discarded: %s", self.path)
