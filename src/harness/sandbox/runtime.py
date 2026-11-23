"""Run one command in a short-lived container and always remove it.

The API process does not call these Docker helpers. The executor process does.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

from src.harness.deadline import DeadlineExceededError, deadline_exceeded
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.policy import build_docker_run

logger = logging.getLogger(__name__)

_HOST_WARNING_EMITTED = False
last_sandbox_block_reason = ""

_HARDENED_BLOCK = (
    "The API process can reach the Docker socket, so this deployment is not hardened. "
    "Generated code was not executed and was not run on the host."
)


class CommandResult:
    """Subset of subprocess.CompletedProcess used by the tool runners."""

    def __init__(self, returncode: int, stdout: str, stderr: str, truncated: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.truncated = truncated


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


def api_can_reach_docker_socket() -> bool:
    """True when this process can use the host Docker daemon.

    A mounted socket that answers a connect, a working docker CLI, or DOCKER_HOST
    all count. Existence without permission does not: that process cannot use it.
    """
    if os.environ.get("DOCKER_HOST", "").strip():
        return True
    sock_path = Path("/var/run/docker.sock")
    if sock_path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(str(sock_path))
        except OSError:
            return False
        probe.close()
        return True
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        completed = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def local_docker_ready() -> bool:
    """True when this process's docker CLI can talk to a daemon. Executor only."""
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


def local_image_present(image: str) -> bool:
    """True when the image is already local. This does not pull. Executor only."""
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


def docker_ready() -> bool:
    """API-side check. False when this process can drive Docker or the executor is down."""
    global last_sandbox_block_reason
    last_sandbox_block_reason = ""
    if api_can_reach_docker_socket():
        last_sandbox_block_reason = _HARDENED_BLOCK
        return False
    from src.harness.sandbox.client import ping

    try:
        status = ping()
    except (OSError, SandboxUnavailableError, DeadlineExceededError):
        return False
    return bool(status.get("ok"))


def image_present(image: str) -> bool:
    """API-side check. The executor reports its own configured image. The client does not choose one."""
    from src.harness.sandbox.client import ping

    try:
        status = ping()
    except (OSError, SandboxUnavailableError, DeadlineExceededError):
        return False
    return bool(status.get("ok")) and bool(status.get("image_present")) and status.get("image") == image


def _drain(pipe, chunks: list[str], limit: int, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = pipe.read(2048)
            if not chunk:
                return
            chunks.append(chunk)
            if sum(len(item) for item in chunks) > limit:
                overflow.set()
                return
    finally:
        pipe.close()


def execute_in_docker(
    argv: list[str],
    *,
    workspace: str,
    timeout: int,
    attempt_id: str,
    cancel_event: threading.Event | None = None,
    max_output: int = 16_384,
) -> CommandResult:
    """Run argv in a new container. Raise if Docker cannot run it. Do not fall back to the host."""
    if not local_docker_ready():
        raise SandboxUnavailableError(
            "SANDBOX_MODE=docker but the Docker daemon is not reachable from the executor. "
            "Generated code was not executed."
        )
    from src.config import settings

    if not local_image_present(settings.SANDBOX_IMAGE):
        raise SandboxUnavailableError(
            f"Sandbox image {settings.SANDBOX_IMAGE} is not present locally. "
            "Build it with `docker compose --profile sandbox build sandbox`. "
            "The image was not pulled and the command was not run on the host.",
            code="sandbox_image_missing",
        )
    name = f"agentforge-sbx-{uuid.uuid4().hex[:12]}"
    command = build_docker_run(workspace, argv, name=name, attempt_id=attempt_id)
    logger.info("Starting sandbox container")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    overflow = threading.Event()
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout_chunks, max_output, overflow), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr_chunks, max_output, overflow), daemon=True),
    ]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    timed_out = False
    cancelled = False
    try:
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process.kill()
                break
            if overflow.is_set() or deadline_exceeded() or time.monotonic() - started > timeout:
                timed_out = not (cancel_event is not None and cancel_event.is_set())
                process.kill()
                break
            time.sleep(0.2)
        for reader in readers:
            reader.join(timeout=5)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30, check=False)
    stdout = "".join(stdout_chunks)[:max_output]
    stderr = "".join(stderr_chunks)[:max_output]
    if cancelled:
        raise SandboxUnavailableError("Sandbox client disconnected. The container was removed.")
    if timed_out:
        if deadline_exceeded():
            raise DeadlineExceededError("PGE deadline exceeded while a sandbox container was running")
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    if process.returncode == 125 or "Cannot connect to the Docker daemon" in stderr:
        raise SandboxUnavailableError("Docker failed to start the sandbox")
    return CommandResult(process.returncode or 0, stdout, stderr, truncated=overflow.is_set())


def reap_instance_containers(instance_id: str) -> None:
    """Remove sandbox containers labeled with this installation id only.

    The id is the executor's own SANDBOX_EXECUTOR_ID. A client request cannot
    select it. Containers that were still running lose their result; that is a
    failure, not a host fallback. Other installation ids are left alone.
    """
    from src.harness.sandbox.policy import checked_executor_id

    token = checked_executor_id(instance_id)
    if shutil.which("docker") is None:
        return
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=agentforge.managed=1",
            "--filter",
            f"label=agentforge.executor={token}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ids = [line for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return
    subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True, timeout=30, check=False)


def cleanup_attempt(attempt_id: str) -> None:
    """The API must not delete containers by an attempt id it chooses."""
    del attempt_id
