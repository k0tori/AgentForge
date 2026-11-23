"""Real Docker sandbox. This file fails when the daemon or image cannot run."""
from __future__ import annotations

import os
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

import pytest

from src.harness.sandbox.runtime import execute_in_docker

pytestmark = [pytest.mark.sandbox, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _executor_instance(monkeypatch):
    monkeypatch.setattr("src.config.settings.SANDBOX_EXECUTOR_ID", "docker-sandbox-test")

IMAGE = "agentforge-sandbox:latest"


def _require_docker() -> None:
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10, check=False)
    if probe.returncode != 0:
        pytest.fail(
            "Docker daemon is required for sandbox verification. "
            "This test is not skipped and was not replaced with a mock.\n"
            f"{probe.stderr.strip()}"
        )


def _build_image() -> None:
    root = Path(__file__).resolve().parents[2]
    build = subprocess.run(
        ["docker", "build", "-f", "Dockerfile.sandbox", "-t", IMAGE, "."],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(f"Failed to build {IMAGE}:\n{build.stderr[-2000:]}")


def test_container_runs_pytest_without_host_secrets_or_socket(tmp_path: Path, monkeypatch):
    _require_docker()
    _build_image()
    monkeypatch.setattr("src.config.settings.SANDBOX_IMAGE", IMAGE)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "host-only-secret")
    host_only = tmp_path / "host-only-secret.txt"
    host_only.write_text("visible-on-host")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_inside.py").write_text(
        textwrap.dedent(
            """
            def test_workspace_file_is_visible():
                assert open("marker.txt", encoding="utf-8").read() == "inside"
            """
        )
    )
    (workspace / "marker.txt").write_text("inside")
    host_secret = str(host_only)
    (workspace / "test_probe.py").write_text(
        textwrap.dedent(
            f"""
            def test_sandbox_is_closed():
                import os, pathlib, socket
                env = os.environ
                assert "DEEPSEEK_API_KEY" not in env
                assert "host-only-secret" not in "\\n".join(env.values())
                assert not pathlib.Path({host_secret!r}).exists()
                sock = socket.socket()
                sock.settimeout(2)
                try:
                    sock.connect(("1.1.1.1", 443))
                except OSError:
                    pass
                else:
                    raise AssertionError("network should be disabled")
            """
        ),
        encoding="utf-8",
    )
    attempt = f"probe-{uuid.uuid4().hex[:8]}"
    result = execute_in_docker(
        ["python", "-m", "pytest", "-v", "--tb=short", "test_probe.py"],
        workspace=str(workspace),
        timeout=60,
        attempt_id=attempt,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in (result.stdout + result.stderr)

    tests = execute_in_docker(
        ["python", "-m", "pytest", "-v", "--tb=short", "test_inside.py"],
        workspace=str(workspace),
        timeout=60,
        attempt_id=attempt,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert "1 passed" in (tests.stdout + tests.stderr)

    leftover = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=agentforge.attempt={attempt}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert leftover.stdout.strip() == ""
    assert os.environ["DEEPSEEK_API_KEY"] == "host-only-secret"


def test_deadline_kills_the_running_container(tmp_path: Path, monkeypatch):
    """Real container. The PGE deadline must remove it, not only mark a task failed."""
    from src.harness.deadline import DeadlineExceededError, bind_deadline, reset_deadline

    _require_docker()
    _build_image()
    monkeypatch.setattr("src.config.settings.SANDBOX_IMAGE", IMAGE)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_sleep.py").write_text(
        "import time\n\ndef test_sleep():\n    time.sleep(30)\n",
        encoding="utf-8",
    )
    attempt = f"stop-{uuid.uuid4().hex[:8]}"
    token = bind_deadline(2)
    started = time.monotonic()
    try:
        with pytest.raises(DeadlineExceededError):
            execute_in_docker(
                ["python", "-m", "pytest", "-v", "--tb=short", "test_sleep.py"],
                workspace=str(workspace),
                timeout=60,
                attempt_id=attempt,
            )
    finally:
        reset_deadline(token)
    assert time.monotonic() - started < 15
    leftover = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=agentforge.attempt={attempt}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert leftover.stdout.strip() == ""


def test_task_database_volume_survives_a_new_container():
    """Compose stores tasks at /var/lib/agentforge. A new container must still see the file."""
    _require_docker()
    _build_image()
    volume = f"agentforge-tasks-check-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True, text=True)
    try:
        write = subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{volume}:/var/lib/agentforge",
                IMAGE,
                "python", "-c",
                "open('/var/lib/agentforge/tasks.sqlite','w').write('persisted')",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert write.returncode == 0, write.stderr
        read = subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{volume}:/var/lib/agentforge",
                IMAGE,
                "python", "-c",
                "print(open('/var/lib/agentforge/tasks.sqlite').read())",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert read.returncode == 0, read.stderr
        assert read.stdout.strip() == "persisted"
    finally:
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, text=True, check=False)
