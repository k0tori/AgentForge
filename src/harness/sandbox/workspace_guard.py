"""Keep a sandbox mount inside one child of a root the API user cannot replace.

The root is a host directory bind-mounted at the same absolute path into the
API and the executor. It is owned by root and is not writable by the API uid,
so that user cannot unlink an attempt directory and replace it with a symlink
before dockerd opens the path. Opening the child with O_NOFOLLOW and comparing
lstat with fstat is the check immediately before the mount. It is not a single
realpath of a caller-supplied path.

This does not inspect POSIX ACLs or Linux capabilities. A process that can
write the parent (root, or an ACL this check does not read) can still swap the
directory in the gap before dockerd opens it. The executor refuses to start
when the API uid can write the parent through owner, group, or other bits.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.protocol import NAME_RE

_BANNED_ROOTS = frozenset({
    Path("/"),
    Path("/tmp"),
    Path("/var"),
    Path("/var/run"),
    Path("/run"),
    Path("/home"),
    Path("/etc"),
    Path("/usr"),
    Path("/dev"),
    Path("/proc"),
    Path("/sys"),
    Path("/bin"),
    Path("/opt"),
})


def api_can_write(path: Path, uid: int, gid: int) -> bool:
    """True when uid/gid can write the directory, or the path is not a real directory."""
    try:
        info = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return True
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o002:
        return True
    if info.st_uid == uid and mode & 0o200:
        return True
    if info.st_gid == gid and mode & 0o020:
        return True
    return False


def assert_workspace_root(root: Path, api_uid: int, api_gid: int) -> Path:
    if root.is_symlink():
        raise SandboxUnavailableError("Workspace root is a symlink")
    resolved = root.resolve()
    if resolved in _BANNED_ROOTS or resolved.parent == Path("/") or len(resolved.parts) < 4:
        raise SandboxUnavailableError(f"Workspace root is not an allowed directory: {resolved}")
    if not resolved.is_dir():
        raise SandboxUnavailableError("Workspace root is not a directory")
    if api_can_write(resolved, api_uid, api_gid):
        raise SandboxUnavailableError(
            "Workspace root is writable by the API user, so an attempt directory could be replaced"
        )
    return resolved


def prepare_root(root: Path, api_uid: int, api_gid: int) -> Path:
    """Create the dedicated root and, as root, take away the API user's write bit."""
    root.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0 and not root.is_symlink():
        os.lchown(root, 0, 0)
        os.chmod(root, 0o755)
    return assert_workspace_root(root, api_uid, api_gid)


def create_attempt(root: Path, api_uid: int, api_gid: int) -> str:
    """Create one unguessable child. The caller does not choose the name."""
    import secrets

    checked = assert_workspace_root(root, api_uid, api_gid)
    name = secrets.token_hex(16)
    path = checked / name
    os.mkdir(path, 0o700)
    if os.geteuid() == 0:
        os.lchown(path, api_uid, api_gid)
    elif os.geteuid() != api_uid:
        os.rmdir(path)
        raise SandboxUnavailableError("Executor cannot hand the workspace to the API user")
    descriptor, _ = open_attempt(checked, name, api_uid, api_gid)
    os.close(descriptor)
    return name


def open_attempt(root: Path, name: str, api_uid: int, api_gid: int) -> tuple[int, Path]:
    """Return a no-follow fd for one real child directory. Caller closes it."""
    if NAME_RE.fullmatch(name) is None:
        raise SandboxUnavailableError("Attempt name is not an executor-issued id")
    checked = assert_workspace_root(root, api_uid, api_gid)
    child = checked / name
    try:
        before = child.lstat()
    except FileNotFoundError as exc:
        raise SandboxUnavailableError("Attempt directory does not exist") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise SandboxUnavailableError("Attempt path is not a real directory")
    try:
        descriptor = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise SandboxUnavailableError("Attempt path cannot be opened without following a symlink") from exc
    try:
        opened = os.fstat(descriptor)
        again = child.lstat()
        if (
            stat.S_ISLNK(again.st_mode)
            or not stat.S_ISDIR(again.st_mode)
            or opened.st_ino != again.st_ino
            or opened.st_dev != again.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_dev != before.st_dev
        ):
            raise SandboxUnavailableError("Attempt directory changed while it was being checked")
        if api_can_write(checked, api_uid, api_gid):
            raise SandboxUnavailableError("Workspace root became writable by the API user")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, child
