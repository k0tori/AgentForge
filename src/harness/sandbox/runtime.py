"""Run one command in a short-lived container and always remove it."""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid

from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.policy import build_docker_run

logger = logging.getLogger(__name__)

_HOST_WARNING_EMITTED = False


class CommandResult:
    """Subset of subprocess.CompletedProcess used by the tool runners."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def warn_host_mode_once() -> None:
    """Host mode is an explicit opt-in. Say so every process, not on every command."""
    global _HOST_WARNING_EMITTED
    if _HOST_WARNING_EMITTED:
        return
    _HOST_WARNING_EMITTED = True
    logger.warning(
        "SANDBOX_MODE=host runs generated code, pytest, and ruff in this process's "
        "environment. They can read this machine's files and environment. "
        "Use SANDBOX_MODE=docker unless you are doing local development."
    )


def docker_ready() -> bool:
    """True only when the docker CLI can talk to a daemon."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def execute_in_docker(argv: list[str], *, workspace: str, timeout: int, attempt_id: str) -> CommandResult:
    """Run argv in a new container. Raise if Docker cannot run it. Do not fall back to the host."""
    if not docker_ready():
        raise SandboxUnavailableError(
            "SANDBOX_MODE=docker but the Docker daemon is not reachable. "
            "Generated code was not executed. Start Docker or set SANDBOX_MODE=host "
            "only for a trusted local workspace."
        )
    name = f"agentforge-sbx-{uuid.uuid4().hex[:12]}"
    command = build_docker_run(workspace, argv, name=name, attempt_id=attempt_id)
    logger.info("Starting sandbox container %s", name)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=False)

    if completed.returncode == 125 or "Cannot connect to the Docker daemon" in completed.stderr:
        raise SandboxUnavailableError(completed.stderr.strip() or "Docker failed to start the sandbox")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def cleanup_attempt(attempt_id: str) -> None:
    """Remove containers still labeled for this attempt. Missing Docker is not a host fallback."""
    if shutil.which("docker") is None:
        return
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=agentforge.attempt={attempt_id}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ids = [line for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return
    subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True, timeout=30, check=False)
