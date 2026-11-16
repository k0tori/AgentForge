from __future__ import annotations

from src.harness.safety.hooks import guard_tool_call, pre_write_hook

WORKSPACE = "/tmp/test_sprint_workspace"


def test_valid_path_allowed():
    """Valid path within workspace should be allowed."""
    result = pre_write_hook(f"{WORKSPACE}/src/main.py", "print('hello')", WORKSPACE)
    assert result.exit_code == 0


def test_system_file_rejected():
    """System files should be rejected."""
    result = pre_write_hook("/etc/passwd", "malicious", WORKSPACE)
    assert result.exit_code == 2
    # On Windows, /etc/passwd is caught by workspace check; on Linux, by system file check
    assert "system" in result.reason.lower() or "workspace" in result.reason.lower()


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


def test_guard_blocks_reads_outside_codebase():
    """Reads must stay inside the codebase or the sprint workspace."""
    result = guard_tool_call(
        "read_file",
        {"path": "/etc/passwd"},
        sprint_workspace=WORKSPACE,
        codebase_path=WORKSPACE,
    )
    assert result.exit_code == 2


def test_guard_blocks_pytest_options():
    """test_file cannot smuggle pytest flags."""
    result = guard_tool_call(
        "run_tests",
        {"path": WORKSPACE, "test_file": "--override-ini=addopts="},
        sprint_workspace=WORKSPACE,
        codebase_path=WORKSPACE,
    )
    assert result.exit_code == 2


def test_guard_allows_workspace_write():
    result = guard_tool_call(
        "write_file",
        {"path": f"{WORKSPACE}/src/tag.py", "content": "x = 1"},
        sprint_workspace=WORKSPACE,
        codebase_path="/tmp/other-codebase",
    )
    assert result.exit_code == 0
