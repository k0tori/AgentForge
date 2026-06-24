"""Unit tests for AST-based code chunking."""
from __future__ import annotations

import os
import tempfile
import textwrap

import pytest

from src.retrieval.chunking import CodeChunk, chunk_directory, chunk_file


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_file(directory: str, name: str, content: str) -> str:
    """Write a file and return its path."""
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


class TestChunkFile:
    """Tests for chunk_file function."""

    def test_function_chunk(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            def greet(name: str) -> str:
                '''Say hello.'''
                return f"Hello {name}"
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) == 1
        assert func_chunks[0].chunk_name == "greet"
        assert "def greet" in func_chunks[0].content
        assert "Say hello" in func_chunks[0].content

    def test_class_chunk(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            class Animal:
                '''A creature.'''
                def speak(self) -> str:
                    '''Make a sound.'''
                    return "..."
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0].chunk_name == "Animal"
        assert "class Animal" in class_chunks[0].content
        assert "def speak" in class_chunks[0].content
        assert "speak" in class_chunks[0].metadata["methods"]

    def test_module_chunk_with_imports(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            '''My module.'''
            import os
            from pathlib import Path

            X = 1
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        module_chunks = [c for c in chunks if c.chunk_type == "module"]
        assert len(module_chunks) == 1
        assert module_chunks[0].chunk_name == "mod"
        assert "import os" in module_chunks[0].content
        assert "My module" in module_chunks[0].content

    def test_empty_file(self, tmp_dir):
        path = _write_file(tmp_dir, "empty.py", "")
        chunks = chunk_file(path, project_root=tmp_dir)
        assert chunks == []

    def test_syntax_error_file(self, tmp_dir):
        path = _write_file(tmp_dir, "bad.py", "def broken(:\n")
        chunks = chunk_file(path, project_root=tmp_dir)
        assert chunks == []

    def test_relative_path_in_metadata(self, tmp_dir):
        path = _write_file(tmp_dir, "sub/mod.py", "def f(): pass")
        chunks = chunk_file(path, project_root=tmp_dir)
        assert any(c.file_path.startswith("sub") for c in chunks)

    def test_multiple_functions(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            def a():
                pass
            def b():
                pass
            def c():
                pass
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        func_names = [c.chunk_name for c in chunks if c.chunk_type == "function"]
        assert func_names == ["a", "b", "c"]

    def test_async_function(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            async def fetch(url: str) -> str:
                '''Fetch a URL.'''
                return "data"
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) == 1
        assert "async def fetch" in func_chunks[0].content

    def test_decorated_function(self, tmp_dir):
        path = _write_file(tmp_dir, "mod.py", """
            import functools
            @functools.lru_cache
            def cached(x: int) -> int:
                return x * 2
        """)
        chunks = chunk_file(path, project_root=tmp_dir)
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) == 1
        assert "@functools.lru_cache" in func_chunks[0].content


class TestChunkDirectory:
    """Tests for chunk_directory function."""

    def test_basic_directory(self, tmp_dir):
        _write_file(tmp_dir, "a.py", "def f(): pass")
        _write_file(tmp_dir, "b.py", "class C: pass")
        chunks = chunk_directory(tmp_dir)
        names = [c.chunk_name for c in chunks]
        assert "f" in names
        assert "C" in names

    def test_skips_hidden_dirs(self, tmp_dir):
        _write_file(tmp_dir, "visible.py", "def f(): pass")
        _write_file(tmp_dir, ".hidden/secret.py", "def s(): pass")
        chunks = chunk_directory(tmp_dir)
        assert all(".hidden" not in c.file_path for c in chunks)

    def test_skips_pycache(self, tmp_dir):
        _write_file(tmp_dir, "mod.py", "def f(): pass")
        _write_file(tmp_dir, "__pycache__/cached.py", "def c(): pass")
        chunks = chunk_directory(tmp_dir)
        assert all("__pycache__" not in c.file_path for c in chunks)

    def test_skips_non_python(self, tmp_dir):
        _write_file(tmp_dir, "mod.py", "def f(): pass")
        _write_file(tmp_dir, "readme.txt", "not python")
        chunks = chunk_directory(tmp_dir)
        assert all(c.file_path.endswith(".py") for c in chunks)

    def test_nested_directories(self, tmp_dir):
        _write_file(tmp_dir, "pkg/__init__.py", "")
        _write_file(tmp_dir, "pkg/sub/mod.py", "def deep(): pass")
        chunks = chunk_directory(tmp_dir)
        names = [c.chunk_name for c in chunks if c.chunk_type == "function"]
        assert "deep" in names

    def test_toy_repo_chunks(self):
        """Integration-style test: verify chunking works on the actual toy-repo."""
        toy_repo = os.path.abspath("toy-repo")
        if not os.path.isdir(toy_repo):
            pytest.skip("toy-repo not found")
        chunks = chunk_directory(toy_repo)
        assert len(chunks) > 0
        # Should have all three types
        types = {c.chunk_type for c in chunks}
        assert "function" in types
        assert "class" in types
        assert "module" in types
