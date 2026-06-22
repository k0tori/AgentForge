from __future__ import annotations

import subprocess
from pathlib import Path

from src.tools.registry import registry


def run_tests(path: str, test_file: str | None = None) -> dict:
    """Execute pytest in the given directory. Returns pass/fail counts and output."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
    if test_file:
        cmd.append(test_file)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")
        return {
            "passed": passed,
            "failed": failed,
            "exit_code": result.returncode,
            "output": output[-3000:],  # truncate long output
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "exit_code": -1, "output": "Test execution timed out (120s)"}


def run_lint(path: str) -> dict:
    """Execute ruff check in the given directory. Returns issues list."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    try:
        result = subprocess.run(
            ["python", "-m", "ruff", "check", "--output-format=text", "."],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=30,
        )
        issues = [line for line in result.stdout.splitlines() if line.strip()]
        return {
            "issues": issues[:50],  # cap at 50 issues
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
