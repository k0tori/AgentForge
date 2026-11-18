"""Policy checks for the sandbox command. These do not start a container."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.policy import assert_workspace_mountable, build_docker_run
from src.tools.test_ops import run_tests


def test_default_mode_is_docker():
    assert Settings.model_fields["SANDBOX_MODE"].default == "docker"


def test_run_command_has_no_socket_or_host_secrets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-secret")
    command = build_docker_run(
        str(tmp_path),
        ["python", "-m", "pytest", "-q"],
        name="agentforge-sbx-policy",
        attempt_id="generate-task-abc",
    )
    rendered = " ".join(command)
    assert "docker.sock" not in rendered
    assert "super-secret" not in rendered
    assert "also-secret" not in rendered
    assert "DEEPSEEK_API_KEY" not in command
    assert command[command.index("--network") + 1] == "none"
    assert "ALL" in command
    assert "--privileged" not in command
    assert command[command.index("-v") + 1].endswith(":/workspace:rw")
    assert command[-4:] == ["python", "-m", "pytest", "-q"]


def test_refuses_system_mounts():
    with pytest.raises(SandboxUnavailableError):
        assert_workspace_mountable("/")
    with pytest.raises(SandboxUnavailableError):
        assert_workspace_mountable("/var/run")


def test_docker_mode_does_not_fall_back_to_the_host(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.tools.test_ops.settings.SANDBOX_MODE", "docker")
    monkeypatch.setattr("src.harness.sandbox.runtime.docker_ready", lambda: False)

    def host_run(*_args, **_kwargs):
        raise AssertionError("untrusted command ran on the host")

    monkeypatch.setattr("src.tools.test_ops.subprocess.run", host_run)
    with pytest.raises(SandboxUnavailableError):
        run_tests(str(tmp_path), allowed_workspace=str(tmp_path))
