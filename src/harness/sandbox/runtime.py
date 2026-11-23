"""Run one command in a short-lived container and always remove it."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid

from src.harness.deadline import DeadlineExceededError, deadline_exceeded
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


def image_present(image: str) -> bool:
    """True only when the image is already local. This does not pull."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=10,
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
    from src.config import settings

    if not image_present(settings.SANDBOX_IMAGE):
        raise SandboxUnavailableError(
            f"Sandbox image {settings.SANDBOX_IMAGE} is not present locally. "
            "Build it with `docker compose --profile sandbox build sandbox`. "
            "The image was not pulled and the command was not run on the host.",
            code="sandbox_image_missing",
        )
    name = f"agentforge-sbx-{uuid.uuid4().hex[:12]}"
    command = build_docker_run(workspace, argv, name=name, attempt_id=attempt_id)
    logger.info("Starting sandbox container %s", name)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    timed_out = False
    try:
        while process.poll() is None:
            if deadline_exceeded() or time.monotonic() - started > timeout:
                timed_out = True
                process.kill()
                break
            time.sleep(0.2)
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        timed_out = True
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=False)
    if timed_out:
        if deadline_exceeded():
            raise DeadlineExceededError("PGE deadline exceeded while a sandbox container was running")
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)

    if process.returncode == 125 or "Cannot connect to the Docker daemon" in stderr:
        raise SandboxUnavailableError(stderr.strip() or "Docker failed to start the sandbox")
    return CommandResult(process.returncode or 0, stdout, stderr)


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
