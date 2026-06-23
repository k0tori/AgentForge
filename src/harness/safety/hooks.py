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
    normalized = Path(file_path).resolve().as_posix()
    return any(normalized.startswith(sp) for sp in SYSTEM_PATHS)


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
