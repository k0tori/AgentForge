from __future__ import annotations

from pathlib import Path

from src.tools.registry import registry


def read_file(path: str) -> str:
    """Read a file and return its content as a string.

    Handles binary files gracefully by returning an error message.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not p.is_file():
        raise IsADirectoryError(f"Not a file: {path}")
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"[Binary file: {path} - cannot read as text]"


def list_directory(path: str, max_depth: int = 3) -> str:
    """List directory tree up to max_depth levels."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    lines: list[str] = []

    def _walk(current: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(current.iterdir(), key=lambda x: (x.is_file(), x.name))
        for entry in entries:
            if entry.name.startswith(".") or entry.name in ("__pycache__", "node_modules", ".git"):
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "│   " if entry != entries[-1] else "    "
                _walk(entry, prefix + extension, depth + 1)

    lines.append(p.name + "/")
    _walk(p, "", 1)
    return "\n".join(lines)


def write_file(path: str, content: str) -> str:
    """Create or overwrite a file. Returns confirmation message."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"


# Register tools
registry.register(
    name="read_file",
    func=read_file,
    description="Read a file and return its content as a string.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)

registry.register(
    name="list_directory",
    func=list_directory,
    description="List directory tree up to max_depth levels.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)

registry.register(
    name="write_file",
    func=write_file,
    description="Create or overwrite a file with the given content.",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
)
