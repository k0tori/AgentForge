"""Real Docker sandbox. This file fails when the daemon or image cannot run."""
from __future__ import annotations

import os
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

from src.harness.sandbox.runtime import execute_in_docker

pytestmark = [pytest.mark.sandbox, pytest.mark.integration]

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

    script = textwrap.dedent(
        f"""
        import os, pathlib, socket
        env = os.environ
        assert "DEEPSEEK_API_KEY" not in env
        assert "host-only-secret" not in "\\n".join(env.values())
        leaked = pathlib.Path({host_secret!r})
        assert not leaked.exists()
        sock = socket.socket()
        sock.settimeout(2)
        try:
            sock.connect(("1.1.1.1", 443))
        except OSError:
            pass
        else:
            raise SystemExit("network should be disabled")
        print("sandbox-ok")
        """
    )
    attempt = f"probe-{uuid.uuid4().hex[:8]}"
    result = execute_in_docker(
        ["python", "-c", script],
        workspace=str(workspace),
        timeout=60,
        attempt_id=attempt,
    )
    assert result.returncode == 0, result.stderr
    assert "sandbox-ok" in result.stdout

    tests = execute_in_docker(
        ["python", "-m", "pytest", "-q", "test_inside.py"],
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
