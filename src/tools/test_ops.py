from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path

from src.config import settings
from src.harness.safety.hooks import is_within_workspace
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.runtime import execute_in_docker, warn_host_mode_once
from src.harness.sandbox.session import current_session
from src.tools.registry import registry

# Truncation limits for tool output
MAX_TEST_OUTPUT_CHARS = 3000
MAX_LINT_ISSUES = 50


def _validate_path(path: str, allowed_workspace: str | None) -> None:
    """Validate path exists and is within the allowed workspace."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if allowed_workspace and not is_within_workspace(path, allowed_workspace):
        raise PermissionError(
            f"Path '{path}' is outside allowed workspace: '{allowed_workspace}'"
        )


def _container_target(path: Path, workspace: Path) -> str:
    """Workspace-relative path used inside the container. Host absolute paths stay on the host."""
    relative = path.resolve().relative_to(workspace.resolve())
    return relative.as_posix() or "."


def _invoke(argv: list[str], workspace: Path, timeout: int) -> subprocess.CompletedProcess[str] | object:
    """Run argv in Docker, or on the host only when SANDBOX_MODE=host."""
    mode = settings.SANDBOX_MODE.strip().lower()
    if mode == "host":
        warn_host_mode_once()
        host_argv = [sys.executable, *argv[1:]]
        return subprocess.run(
            host_argv,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if mode != "docker":
        raise SandboxUnavailableError(
            f"Unknown SANDBOX_MODE={settings.SANDBOX_MODE!r}. Use 'docker' or the explicit 'host' opt-in."
        )
    session = current_session()
    attempt_id = session.attempt_id if session is not None else f"exec-{uuid.uuid4().hex[:8]}"
    return execute_in_docker(argv, workspace=str(workspace), timeout=timeout, attempt_id=attempt_id)


def run_tests(path: str, test_file: str | None = None, allowed_workspace: str | None = None) -> dict:
    """Execute pytest in the given directory. Returns pass/fail counts and output."""
    _validate_path(path, allowed_workspace)
    p = Path(path)

    cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
    if test_file:
        if str(test_file).startswith("-"):
            return {
                "passed": 0,
                "failed": 0,
                "exit_code": 2,
                "output": "Refusing pytest option passed as test_file",
            }
        candidate = Path(test_file)
        if not candidate.is_absolute():
            candidate = p / candidate
        if allowed_workspace and not is_within_workspace(str(candidate), allowed_workspace):
            raise PermissionError(f"test_file '{test_file}' is outside allowed workspace")
        cmd.append(_container_target(candidate, p))

    try:
        result = _invoke(cmd, p, timeout=120)
        output = result.stdout + result.stderr

        # Parse pytest summary line (more robust than counting occurrences)
        # Examples: "5 passed, 2 failed", "5 passed", "2 failed, 1 error"
        passed = 0
        failed = 0

        # Try to parse the summary line first
        summary_match = re.search(r'(\d+)\s+passed', output)
        if summary_match:
            passed = int(summary_match.group(1))

        summary_match = re.search(r'(\d+)\s+failed', output)
        if summary_match:
            failed = int(summary_match.group(1))

        # Fallback to counting if no summary found
        if passed == 0 and failed == 0:
            passed = output.count(" PASSED")
            failed = output.count(" FAILED")

        return {
            "passed": passed,
            "failed": failed,
            "exit_code": result.returncode,
            "output": output[-MAX_TEST_OUTPUT_CHARS:],
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "exit_code": -1, "output": "Test execution timed out (120s)"}


def run_lint(path: str, allowed_workspace: str | None = None) -> dict:
    """Execute ruff check in the given directory. Returns issues list."""
    _validate_path(path, allowed_workspace)
    p = Path(path)

    try:
        result = _invoke(
            ["python", "-m", "ruff", "check", "--output-format=text", "."],
            p,
            timeout=30,
        )
        issues = [line for line in result.stdout.splitlines() if line.strip()]
        return {
            "issues": issues[:MAX_LINT_ISSUES],
            "exit_code": result.returncode,
            "issue_count": len(issues),
        }
    except subprocess.TimeoutExpired:
        return {"issues": ["Lint timed out (30s)"], "exit_code": -1, "issue_count": 0}


registry.register(
    name="run_tests",
    func=run_tests,
    description="Execute pytest in the given directory. Returns pass/fail counts and output.",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)

registry.register(
    name="run_lint",
    func=run_lint,
    description="Execute ruff lint check. Returns list of issues found.",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
