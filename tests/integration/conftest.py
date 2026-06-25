"""Shared fixtures for integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.llm.client import LLMClient
from src.workflow.state import AgentState


# Fixture paths
FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCKS_DIR = Path(__file__).parent / "mocks"


@pytest.fixture
def toy_repo_path(tmp_path: Path) -> Path:
    """Create a minimal toy repo structure for testing."""
    repo = tmp_path / "toy-repo"
    repo.mkdir()

    # Create CONVENTIONS.md
    conventions = repo / "CONVENTIONS.md"
    conventions.write_text("""# Conventions

## Naming
- Models: PascalCase (e.g., `User`, `Note`)
- Tables: snake_case (e.g., `users`, `notes`)
- Routes: kebab-case (e.g., `/api/v1/users`)

## Error Handling
- Use custom exceptions mapped to HTTP status codes
- 404 for not found, 400 for validation errors

## Testing
- Use pytest fixtures for database setup
- Parametrize tests for multiple scenarios
""")

    # Create minimal source structure
    src_dir = repo / "src"
    src_dir.mkdir()

    models_dir = src_dir / "models"
    models_dir.mkdir()

    # Create user.py model
    user_model = models_dir / "user.py"
    user_model.write_text("""from sqlmodel import SQLModel, Field
from typing import Optional

class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(max_length=50, unique=True)
    email: str = Field(max_length=100)
""")

    # Create note.py model
    note_model = models_dir / "note.py"
    note_model.write_text("""from sqlmodel import SQLModel, Field
from typing import Optional

class Note(SQLModel, table=True):
    __tablename__ = "notes"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=100)
    content: str
    user_id: int = Field(foreign_key="users.id")
""")

    # Create empty routers/services/schemas directories
    for subdir in ["routers", "services", "schemas"]:
        (src_dir / subdir).mkdir()
        (src_dir / subdir / "__init__.py").touch()

    # Create tests directory
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").touch()

    return repo


@pytest.fixture
def initial_state(toy_repo_path: Path) -> AgentState:
    """Create initial state for a Tag task."""
    return {
        "request": "Add a Tag resource following existing patterns, with Note-Tag many-to-many relationship",
        "plan": [],
        "sprint_contract": [],
        "execution_trace": [],
        "code_diff": "",
        "eval_feedback": None,
        "retry_count": 0,
        "task_id": "test-task-001",
        "codebase_path": str(toy_repo_path),
        "current_sprint": 1,
        "final_verdict": None,
        "error": None,
    }


@pytest.fixture
def mock_llm_client() -> LLMClient:
    """Create a mock LLM client that returns pre-recorded responses."""
    client = MagicMock(spec=LLMClient)
    client.total_tokens_used = 0
    client.chat = AsyncMock()
    client.build_messages = MagicMock(side_effect=lambda sys, user, hist=None: [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ])
    return client


def load_mock_response(filename: str) -> dict:
    """Load a mock response from the mocks directory."""
    filepath = MOCKS_DIR / filename
    if filepath.exists():
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    return {}


def make_ai_message(content: str, tool_calls: list | None = None) -> AIMessage:
    """Create an AIMessage with optional tool calls."""
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg
