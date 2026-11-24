"""Pure helpers that build a locked-down `docker run` command.

The command is the security boundary: no shell, no host environment, no Docker socket.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.config import settings
from src.harness.sandbox.errors import SandboxUnavailableError

_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MEMORY_RE = re.compile(r"^[0-9]+[bkmg]$", re.IGNORECASE)
_CPU_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_INSTANCE_RE = _ATTEMPT_RE

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


def checked_sandbox_tag(value: str | None = None) -> str:
    """Validate the configured tag. Digest refs and registry pins are not accepted here."""
    image = settings.SANDBOX_IMAGE if value is None else value
    if not isinstance(image, str) or _IMAGE_RE.fullmatch(image) is None or "@" in image:
        raise SandboxUnavailableError(f"Invalid SANDBOX_IMAGE: {image!r}")
    return image


def checked_image_digest(value: str) -> str:
    """Accept only a local image id. This is not a registry name and not a client field."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise SandboxUnavailableError("Refusing to run an image id that is not a sha256 digest")
    return value


def _checked_image() -> str:
    return checked_sandbox_tag()


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


def checked_executor_id(value: str | None = None) -> str:
    """Return the installation id this executor is allowed to reap.

    The id comes from SANDBOX_EXECUTOR_ID, not from a client request. An empty
    or illegal value refuses startup. The same id across two processes is one
    cleanup domain: either restart removes that id's containers.
    """
    raw = settings.SANDBOX_EXECUTOR_ID if value is None else value
    token = raw.strip()
    if not token:
        raise SandboxUnavailableError(
            "SANDBOX_EXECUTOR_ID is empty. The executor will not start and will not remove containers."
        )
    if _INSTANCE_RE.fullmatch(token) is None:
        raise SandboxUnavailableError(
            "SANDBOX_EXECUTOR_ID must be one token of letters, digits, dot, underscore, or hyphen."
        )
    return token


def _checked_attempt(attempt_id: str) -> str:
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        raise SandboxUnavailableError(f"Invalid sandbox attempt id: {attempt_id!r}")
    return attempt_id


def container_argv(argv: list[str]) -> list[str]:
    """Allow only the pytest and ruff vectors the executor builds.

    `python -c`, a shell, and absolute host paths are rejected.
    """
    pytest_prefix = ["python", "-m", "pytest", "-v", "--tb=short"]
    ruff_argv = ["python", "-m", "ruff", "check", "--output-format=concise", "."]
    if argv == ruff_argv:
        return list(argv)
    if argv[: len(pytest_prefix)] != pytest_prefix or len(argv) > len(pytest_prefix) + 1:
        raise SandboxUnavailableError("Sandbox commands must be pytest or ruff")
    if len(argv) == len(pytest_prefix) + 1:
        target = argv[-1]
        if (
            not target
            or target.startswith(("-", "/", "."))
            or ".." in target.split("/")
            or "docker.sock" in target
        ):
            raise SandboxUnavailableError("Refusing sandbox argument")
    return list(argv)


def build_docker_run(
    workspace: str,
    argv: list[str],
    *,
    name: str,
    attempt_id: str,
    image: str | None = None,
) -> list[str]:
    """Return the argv of a one-shot container. The list is not passed through a shell."""
    mount = assert_workspace_mountable(workspace)
    uid = settings.SANDBOX_RUN_UID
    gid = settings.SANDBOX_RUN_GID
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
        "agentforge.managed=1",
        "--label",
        f"agentforge.executor={checked_executor_id()}",
        "--label",
        f"agentforge.attempt={_checked_attempt(attempt_id)}",
        "-v",
        f"{mount}:/workspace:rw",
        "-w",
        "/workspace",
    ]
    for item in CONTAINER_ENV:
        command.extend(["-e", item])
    command.append(checked_image_digest(image) if image is not None else _checked_image())
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
