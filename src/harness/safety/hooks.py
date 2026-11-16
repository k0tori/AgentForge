from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class HookResult:
    """Result of a safety hook check."""

    exit_code: int  # 0 = allow, 2 = reject
    reason: str = ""


# Paths that are always off-limits
SYSTEM_PATHS = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys")

# File patterns that contain secrets
SECRET_PATTERNS = (".env", ".env.", ".key", ".pem", ".p12", ".pfx", "id_rsa", "id_ed25519")


def is_system_file(file_path: str) -> bool:
    """Check if the path points to a system file."""
    resolved = Path(file_path).resolve()
    for system_path in SYSTEM_PATHS:
        try:
            resolved.relative_to(system_path)
            return True
        except ValueError:
            continue
    return False


def is_secret_file(file_path: str) -> bool:
    """Check if the path points to a secret/credential file."""
    name = Path(file_path).name.lower()
    return any(
        name.startswith(pat) or name == pat or name.endswith(pat)
        for pat in SECRET_PATTERNS
    )


def is_within_workspace(file_path: str, workspace: str) -> bool:
    """Check if the file path is within the sprint workspace."""
    try:
        resolved_file = Path(file_path).resolve()
        resolved_workspace = Path(workspace).resolve()
        resolved_file.relative_to(resolved_workspace)
        return True
    except ValueError:
        return False


def pre_write_hook(file_path: str, content: str, sprint_workspace: str) -> HookResult:
    """Pre-write safety hook. Checks file path against security constraints.

    Args:
        file_path: Target file path
        content: Content to be written
        sprint_workspace: The sprint's temporary working directory

    Returns:
        HookResult with exit_code 0 (allow) or 2 (reject)
    """
    # Check 1: Reject writes to system files
    if is_system_file(file_path):
        return HookResult(exit_code=2, reason="Cannot edit system files")

    # Check 2: Reject writes to secret files
    if is_secret_file(file_path):
        return HookResult(exit_code=2, reason="Cannot edit secret/credential files")

    # Check 3: Reject writes outside sprint workspace
    if not is_within_workspace(file_path, sprint_workspace):
        return HookResult(
            exit_code=2,
            reason=f"Cannot write outside sprint workspace: {sprint_workspace}",
        )

    return HookResult(exit_code=0)


def guard_tool_call(
    tool_name: str,
    tool_args: dict,
    *,
    sprint_workspace: str,
    codebase_path: str,
) -> HookResult:
    """Confine tool paths to the sprint workspace and the source codebase.

    Writes and test/lint runs stay inside the sprint workspace. Reads may also
    use the original codebase. Secret files are rejected for every operation.
    """
    read_tools = {"read_file", "list_directory", "search_code"}
    exec_tools = {"run_tests", "run_lint"}
    path = str(tool_args.get("path", ""))

    if tool_name == "write_file":
        return pre_write_hook(path, str(tool_args.get("content", "")), sprint_workspace)

    if tool_name not in read_tools and tool_name not in exec_tools:
        return HookResult(exit_code=0)

    if not path:
        return HookResult(exit_code=2, reason="Missing path")
    if is_secret_file(path) or is_system_file(path):
        return HookResult(exit_code=2, reason="Cannot access secret or system files")

    if tool_name in exec_tools:
        if not is_within_workspace(path, sprint_workspace):
            return HookResult(exit_code=2, reason="Tests and lint must run inside the sprint workspace")
        test_file = tool_args.get("test_file")
        if test_file:
            test_path = Path(str(test_file))
            if str(test_file).startswith("-"):
                return HookResult(exit_code=2, reason="test_file must be a path, not a pytest option")
            if not test_path.is_absolute():
                test_path = Path(sprint_workspace) / test_path
            if not is_within_workspace(str(test_path), sprint_workspace):
                return HookResult(exit_code=2, reason="test_file must stay inside the sprint workspace")
        return HookResult(exit_code=0)

    if is_within_workspace(path, sprint_workspace) or is_within_workspace(path, codebase_path):
        return HookResult(exit_code=0)
    return HookResult(exit_code=2, reason="Path is outside the codebase and sprint workspace")
