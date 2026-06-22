from __future__ import annotations

from src.harness.safety.hooks import pre_write_hook


WORKSPACE = "/tmp/test_sprint_workspace"


def test_valid_path_allowed():
    """Valid path within workspace should be allowed."""
    result = pre_write_hook(f"{WORKSPACE}/src/main.py", "print('hello')", WORKSPACE)
    assert result.exit_code == 0


def test_system_file_rejected():
    """System files should be rejected."""
    result = pre_write_hook("/etc/passwd", "malicious", WORKSPACE)
    assert result.exit_code == 2
    assert "system" in result.reason.lower()


def test_secret_file_rejected():
    """Secret files should be rejected."""
    result = pre_write_hook(f"{WORKSPACE}/.env", "SECRET=123", WORKSPACE)
    assert result.exit_code == 2
    assert "secret" in result.reason.lower()


def test_outside_workspace_rejected():
    """Files outside workspace should be rejected."""
    result = pre_write_hook("/other/path/file.py", "content", WORKSPACE)
    assert result.exit_code == 2
    assert "workspace" in result.reason.lower()


def test_pem_file_rejected():
    """PEM key files should be rejected."""
    result = pre_write_hook(f"{WORKSPACE}/server.pem", "key content", WORKSPACE)
    assert result.exit_code == 2
