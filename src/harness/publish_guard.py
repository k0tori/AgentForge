"""Decide what a sprint may publish onto HEAD.

The result branch is HEAD plus the paths the sprint changed after it was seeded.
Unrelated tracked files, including dot directories, stay as they are on HEAD.
Dirty worktree edits that the sprint did not touch stay in the working tree and
are not committed and not deleted. Secrets and symlink escapes are refused
before any commit or external write.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

SKIP_NAMES = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    "htmlcov",
    "node_modules",
    ".venv",
    "venv",
})
_EXAMPLE_ENV = re.compile(r"^\.env\.(example|sample|template)$", re.IGNORECASE)
_SENSITIVE_NAMES = frozenset({
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".pgpass",
    "credentials.json",
    "secrets.json",
    "service-account.json",
})
_SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube"})
_PRIVATE_KEYS = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")


class PublishRejectedError(RuntimeError):
    """Publish stopped before it could commit or write through a link."""

    def __init__(self, message: str, *, branch: str | None = None) -> None:
        super().__init__(message)
        self.code = "publish_rejected"
        self.branch = branch


def is_sensitive_path(relative: str) -> bool:
    """True for common secret filenames. This does not read the file."""
    parts = Path(relative).parts
    if any(part.lower() in _SENSITIVE_DIRS for part in parts):
        return True
    name = parts[-1]
    lowered = name.lower()
    if lowered in _SENSITIVE_NAMES or lowered in _PRIVATE_KEYS:
        return True
    if lowered.startswith(".env.") and _EXAMPLE_ENV.match(lowered) is None:
        return True
    return lowered.endswith(_SENSITIVE_SUFFIXES)


def scan_tree(root: Path) -> dict[str, dict[str, str]]:
    """File metadata. Symlink targets are the link text, not the file they point at."""
    found: dict[str, dict[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_NAMES]
        for filename in filenames:
            if filename.endswith(".pyc") or filename == ".agentforge-seed.json":
                continue
            full = Path(dirpath) / filename
            relative = full.relative_to(root).as_posix()
            if full.is_symlink():
                found[relative] = {"kind": "symlink", "target": os.readlink(full)}
            elif full.is_file():
                found[relative] = {"kind": "file", "digest": hashlib.sha256(full.read_bytes()).hexdigest()}
    return found


def tree_delta(
    seed: dict[str, dict[str, str]],
    current: dict[str, dict[str, str]],
) -> tuple[list[str], list[str], list[str]]:
    added = [path for path in current if path not in seed]
    modified = [path for path, meta in current.items() if path in seed and seed[path] != meta]
    deleted = [path for path in seed if path not in current]
    return added, modified, deleted


def symlink_escapes(root: Path, relative: str, target: str) -> bool:
    """True when the link text points outside root. Does not read the target file."""
    raw = Path(target)
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = Path(os.path.normpath(str((root / relative).parent / raw)))
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False


def contains_symlink(root: Path, relative: str) -> bool:
    current = root
    if current.is_symlink():
        return True
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def path_and_parents(relative: str) -> list[str]:
    acc: list[str] = []
    current: list[str] = []
    for part in Path(relative).parts:
        current.append(part)
        acc.append("/".join(current))
    return acc


def assert_delta_is_publishable(
    sprint: Path,
    current: dict[str, dict[str, str]],
    added: list[str],
    modified: list[str],
    deleted: list[str],
) -> None:
    """Reject secret introductions and external links before any commit."""
    for relative in [*added, *modified, *deleted]:
        if is_sensitive_path(relative):
            raise PublishRejectedError(
                f"Refusing to publish {relative}: sensitive paths are not added, modified, or removed. "
                "Tracked copies were left unchanged. The file contents were not written to the branch, "
                "the diff, or the task result."
            )
    for relative in [*added, *modified]:
        meta = current[relative]
        full = sprint / relative
        if meta["kind"] == "symlink" or full.is_symlink():
            if symlink_escapes(sprint, relative, meta.get("target", os.readlink(full))):
                raise PublishRejectedError(
                    f"Refusing to publish {relative}: it is a symlink outside the repository. "
                    "The target was not read or written."
                )


def assert_destination_is_safe(worktree: Path, head_symlinks: set[str], relative: str) -> None:
    for candidate in path_and_parents(relative):
        if candidate in head_symlinks or contains_symlink(worktree, candidate):
            raise PublishRejectedError(
                f"Refusing to publish {relative}: HEAD or the publish checkout has a symlink at {candidate}. "
                "Nothing was written through it."
            )


def write_regular_file(root: Path, relative: str, data: bytes) -> None:
    """Create a regular file. O_NOFOLLOW refuses to replace or follow a symlink."""
    current = root
    if current.is_symlink():
        raise PublishRejectedError("Refusing to write through a symlink at the worktree root")
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise PublishRejectedError(f"Refusing to write through symlink {current}")
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise PublishRejectedError(f"Refusing to create {relative} under a non-directory")
    destination = root / relative
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        handle = os.open(destination, flags, 0o644)
    except FileExistsError as exc:
        raise PublishRejectedError(f"Refusing to follow or replace symlink {relative}") from exc
    except OSError as exc:
        if getattr(exc, "errno", None) in {40, 62}:  # ELOOP on Linux / macOS
            raise PublishRejectedError(f"Refusing to follow symlink {relative}") from exc
        raise
    try:
        os.write(handle, data)
    finally:
        os.close(handle)
