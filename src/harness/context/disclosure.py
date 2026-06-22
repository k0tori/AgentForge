from __future__ import annotations

from pathlib import Path

from src.tools.file_ops import list_directory, read_file


class ProgressiveDisclosure:
    """Manages progressive disclosure of codebase information.

    Planner gets a summary first; Generator reads specific files on demand.
    """

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)

    def summarize_codebase(self) -> str:
        """Return a high-level summary: directory tree + entry points."""
        tree = list_directory(str(self.repo_path), max_depth=2)

        # Identify entry point files
        entry_points = []
        for name in ("main.py", "app.py", "__init__.py", "pyproject.toml", "CONVENTIONS.md"):
            p = self.repo_path / name
            if p.exists():
                entry_points.append(name)

        summary_parts = [f"## Directory Structure\n\n```\n{tree}\n```"]

        if entry_points:
            summary_parts.append(f"## Entry Points\n\n{', '.join(entry_points)}")

        return "\n\n".join(summary_parts)

    def read_entry_point(self, filename: str) -> str:
        """Read a specific entry point file."""
        p = self.repo_path / filename
        if not p.exists():
            return f"File not found: {filename}"
        return read_file(str(p))

    @staticmethod
    def get_rubric_summary() -> dict:
        """Return evaluation rubric: dimension names + weights only."""
        return {
            "functional_correctness": {"weight": 0.30, "description": "All contract criteria satisfied"},
            "code_quality": {"weight": 0.20, "description": "Naming, types, conventions compliance"},
            "security": {"weight": 0.20, "description": "No obvious vulnerabilities"},
            "architecture_fit": {"weight": 0.15, "description": "Follows existing layer patterns"},
            "test_coverage": {"weight": 0.15, "description": "Positive and edge case tests present"},
        }

    @staticmethod
    def get_rubric_detail(dimension: str) -> str:
        """Return full scoring criteria for one dimension."""
        details = {
            "functional_correctness": (
                "Check each acceptance criterion in the sprint contract. "
                "For each: does the code_diff implement it? Run tests to verify. "
                "A criterion is PASS only if there's concrete evidence."
            ),
            "code_quality": (
                "Check naming conventions (snake_case for Python, PascalCase for classes). "
                "Verify type annotations on all function signatures. "
                "Check error handling uses custom exceptions, not HTTPException directly. "
                "Verify file organization follows models -> schemas -> services -> routers."
            ),
            "security": (
                "Check for hardcoded secrets or passwords. "
                "Verify password fields are not exposed in response schemas. "
                "Check for SQL injection risks in raw queries. "
                "Verify input validation on all user-facing endpoints."
            ),
            "architecture_fit": (
                "Check that new code follows the same 4-layer pattern as existing resources. "
                "Verify service layer doesn't directly access HTTP concerns. "
                "Check that router only calls service functions."
            ),
            "test_coverage": (
                "Check for positive (happy path) tests. "
                "Check for negative (error) tests: not found, validation errors. "
                "Check for edge cases: empty input, boundary values. "
                "Verify tests use fixtures from conftest.py."
            ),
        }
        return details.get(dimension, f"Unknown dimension: {dimension}")
