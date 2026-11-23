"""Pure helpers that build a locked-down `docker run` command.

The command is the security boundary: no shell, no host environment, no Docker socket.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from src.config import settings
from src.harness.sandbox.errors import SandboxUnavailableError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_MEMORY_RE = re.compile(r"^[0-9]+[bkmg]$", re.IGNORECASE)
_CPU_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# The process inside the container sees only these variables.
CONTAINER_ENV = (
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
    "HOME=/tmp",
    "PATH=/usr/local/bin:/usr/bin:/bin",
)

_FORBIDDEN_MOUNT_NAMES = frozenset({
    "docker.sock",
    "docker.sock/",
})


def assert_workspace_mountable(workspace: str) -> Path:
    """Reject mounts that would expose the Docker socket or a system root."""
    raw = Path(workspace)
    path = raw.resolve()
    if not path.is_dir():
        raise SandboxUnavailableError(f"Sandbox workspace is not a directory: {path}")
    if path.name in _FORBIDDEN_MOUNT_NAMES or "docker.sock" in path.parts or "docker.sock" in raw.parts:
        raise SandboxUnavailableError("Refusing to mount the Docker socket into a sandbox")
    denied = (
        Path("/etc"),
        Path("/usr"),
        Path("/bin"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        Path("/run"),
        Path("/var/run"),
    )
    for candidate in {raw, path}:
        if candidate == Path("/"):
            raise SandboxUnavailableError("Refusing to mount the filesystem root into a sandbox")
        for prefix in denied:
            try:
                candidate.relative_to(prefix)
            except ValueError:
                continue
            raise SandboxUnavailableError(f"Refusing to mount a system directory into a sandbox: {candidate}")
    return path


def _checked_image() -> str:
    image = settings.SANDBOX_IMAGE
    if not _IMAGE_RE.fullmatch(image):
        raise SandboxUnavailableError(f"Invalid SANDBOX_IMAGE: {image!r}")
    return image


def _checked_memory() -> str:
    memory = settings.SANDBOX_MEMORY.strip().lower()
    if not _MEMORY_RE.fullmatch(memory):
        raise SandboxUnavailableError(f"Invalid SANDBOX_MEMORY: {settings.SANDBOX_MEMORY!r}")
    return memory


def _checked_cpus() -> str:
    cpus = str(settings.SANDBOX_CPUS).strip()
    if not _CPU_RE.fullmatch(cpus):
        raise SandboxUnavailableError(f"Invalid SANDBOX_CPUS: {settings.SANDBOX_CPUS!r}")
    return cpus


def _checked_pids() -> str:
    pids = int(settings.SANDBOX_PIDS_LIMIT)
    if not 16 <= pids <= 4096:
        raise SandboxUnavailableError(f"SANDBOX_PIDS_LIMIT out of range: {pids}")
    return str(pids)


def _checked_attempt(attempt_id: str) -> str:
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise SandboxUnavailableError(f"Invalid sandbox attempt id: {attempt_id!r}")
    return attempt_id


def container_argv(argv: list[str]) -> list[str]:
    """Translate a host command into one that runs inside the sandbox image.

    Host interpreters and absolute paths are rejected. Callers pass `python`
    and workspace-relative arguments.
    """
    if not argv or argv[0] != "python":
        raise SandboxUnavailableError("Sandbox commands must start with the image python")
    for arg in argv:
        if "docker.sock" in arg or arg.startswith("/var/run"):
            raise SandboxUnavailableError(f"Refusing sandbox argument: {arg}")
    return list(argv)


def build_docker_run(workspace: str, argv: list[str], *, name: str, attempt_id: str) -> list[str]:
    """Return the argv of a one-shot container. The list is not passed through a shell."""
    mount = assert_workspace_mountable(workspace)
    uid = os.getuid()
    gid = os.getgid()
    command = [
        "docker",
        "run",
        "--pull",
        "never",
        "--name",
        name,
        "--rm",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m",
        "--memory",
        _checked_memory(),
        "--memory-swap",
        _checked_memory(),
        "--cpus",
        _checked_cpus(),
        "--pids-limit",
        _checked_pids(),
        "--user",
        f"{uid}:{gid}",
        "--label",
        "agentforge.sandbox=1",
        "--label",
        f"agentforge.attempt={_checked_attempt(attempt_id)}",
        "-v",
        f"{mount}:/workspace:rw",
        "-w",
        "/workspace",
    ]
    for item in CONTAINER_ENV:
        command.extend(["-e", item])
    command.append(_checked_image())
    command.extend(container_argv(argv))
    _assert_command_is_locked(command)
    return command


def _assert_command_is_locked(command: list[str]) -> None:
    rendered = " ".join(command)
    if "docker.sock" in rendered:
        raise SandboxUnavailableError("Sandbox command tried to reference the Docker socket")
    if "--privileged" in command or "--network" not in command:
        raise SandboxUnavailableError("Sandbox command is missing its network lock")
    network = command[command.index("--network") + 1]
    if network != "none":
        raise SandboxUnavailableError(f"Sandbox network must be none, got {network}")
