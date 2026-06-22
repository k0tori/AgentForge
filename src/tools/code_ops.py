from __future__ import annotations

import re
from pathlib import Path

from src.tools.registry import registry


def search_code(path: str, pattern: str, file_glob: str = "*.py") -> str:
    """Search for a regex pattern in files matching the glob. Returns matching lines with context."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    regex = re.compile(pattern)
    results: list[str] = []
    max_matches = 50

    files = p.rglob(file_glob) if p.is_dir() else [p]
    for filepath in files:
        if filepath.name.startswith(".") or "__pycache__" in str(filepath):
            continue
        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, PermissionError):
            continue

        for i, line in enumerate(lines, 1):
            if regex.search(line):
                rel = filepath.relative_to(p) if p.is_dir() else filepath.name
                results.append(f"{rel}:{i}: {line.strip()}")
                if len(results) >= max_matches:
                    results.append(f"... (stopped at {max_matches} matches)")
                    return "\n".join(results)

    if not results:
        return f"No matches found for pattern '{pattern}' in {path}"
    return "\n".join(results)


registry.register(
    name="search_code",
    func=search_code,
    description="Search for a regex pattern in code files. Returns matching lines with file and line number.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
