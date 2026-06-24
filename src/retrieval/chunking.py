"""AST-based Python code chunking for RAG indexing.

Extracts three chunk types from Python source files:
- function: standalone function definitions (with docstring, signature, body)
- class: class definitions (with docstring, method signatures — no method bodies)
- module: file-level info (imports, module docstring, top-level constants)
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodeChunk:
    """A single chunk of code extracted from a source file."""

    content: str
    chunk_type: str  # "function" | "class" | "module"
    chunk_name: str  # function name, class name, or file name
    file_path: str   # relative path from project root
    metadata: dict = field(default_factory=dict)


def _extract_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract decorator names from a function/method node."""
    decorators = []
    for dec in node.decorator_list:
        decorators.append(ast.unparse(dec))
    return decorators


def _format_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format a function node into a readable string."""
    parts: list[str] = []

    # Decorators
    for dec in _extract_decorators(node):
        parts.append(f"@{dec}")

    # Signature
    is_async = isinstance(node, ast.AsyncFunctionDef)
    prefix = "async " if is_async else ""
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    parts.append(f"{prefix}def {node.name}({args}){returns}:")

    # Docstring
    docstring = ast.get_docstring(node)
    if docstring:
        parts.append(f'    """{docstring}"""')

    # Body (skip docstring expression)
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if body:
        for stmt in body:
            line = ast.unparse(stmt)
            for sub in line.split("\n"):
                parts.append(f"    {sub}")
    else:
        parts.append("    pass")

    return "\n".join(parts)


def _format_class(node: ast.ClassDef) -> str:
    """Format a class node — docstring + method signatures only (no bodies)."""
    parts: list[str] = []

    # Decorators
    for dec in node.decorator_list:
        parts.append(f"@{ast.unparse(dec)}")

    # Class header
    bases = [ast.unparse(b) for b in node.bases]
    base_str = f"({', '.join(bases)})" if bases else ""
    parts.append(f"class {node.name}{base_str}:")

    # Docstring
    docstring = ast.get_docstring(node)
    if docstring:
        parts.append(f'    """{docstring}"""')

    # Method signatures (no body)
    methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if methods:
        for method in methods:
            for dec in _extract_decorators(method):
                parts.append(f"    @{dec}")
            is_async = isinstance(method, ast.AsyncFunctionDef)
            prefix = "async " if is_async else ""
            args = ast.unparse(method.args)
            returns = f" -> {ast.unparse(method.returns)}" if method.returns else ""
            method_doc = ast.get_docstring(method)
            parts.append(f"    {prefix}def {method.name}({args}){returns}:")
            if method_doc:
                parts.append(f'        """{method_doc}"""')
            parts.append("")
    else:
        parts.append("    pass")

    return "\n".join(parts)


def _extract_imports(node: ast.Module) -> list[str]:
    """Extract import statements from a module."""
    return [ast.unparse(child) for child in node.body if isinstance(child, (ast.Import, ast.ImportFrom))]


def chunk_file(file_path: str, project_root: str | None = None) -> list[CodeChunk]:
    """Parse a single Python file and extract code chunks.

    Args:
        file_path: Absolute path to the Python file.
        project_root: Root directory for computing relative paths.

    Returns:
        List of CodeChunk objects.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []

    rel_path = os.path.relpath(file_path, project_root) if project_root else Path(file_path).name
    chunks: list[CodeChunk] = []

    # Extract top-level functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(CodeChunk(
                content=_format_function(node),
                chunk_type="function",
                chunk_name=node.name,
                file_path=rel_path,
                metadata={"lineno": node.lineno, "end_lineno": getattr(node, "end_lineno", None)},
            ))

    # Extract classes
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            method_names = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            chunks.append(CodeChunk(
                content=_format_class(node),
                chunk_type="class",
                chunk_name=node.name,
                file_path=rel_path,
                metadata={"lineno": node.lineno, "methods": method_names},
            ))

    # Module-level chunk (imports + docstring)
    imports = _extract_imports(tree)
    module_doc = ast.get_docstring(tree)
    module_parts: list[str] = []
    if module_doc:
        module_parts.append(f'"""{module_doc}"""')
    if imports:
        module_parts.append("# Imports")
        module_parts.extend(imports)

    if module_parts:
        chunks.append(CodeChunk(
            content="\n".join(module_parts),
            chunk_type="module",
            chunk_name=Path(file_path).stem,
            file_path=rel_path,
            metadata={"lineno": 1},
        ))

    return chunks


def chunk_directory(directory: str) -> list[CodeChunk]:
    """Walk a directory and chunk all Python files.

    Args:
        directory: Root directory to scan.

    Returns:
        List of CodeChunk objects from all .py files.
    """
    all_chunks: list[CodeChunk] = []
    directory = os.path.abspath(directory)

    for root, dirs, files in os.walk(directory):
        # Skip hidden directories and __pycache__
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            all_chunks.extend(chunk_file(fpath, project_root=directory))

    return all_chunks
