"""Integration test with REAL tool execution (filesystem + pytest).

This test verifies the Generator → SprintWorkspace → Evaluator chain
with actual file I/O and real pytest runs. Only the LLM is mocked
(pre-recorded responses) — everything else hits the filesystem.

This is the test that proves the full chain works end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.evaluator import EvaluatorAgent
from src.agents.generator import GeneratorAgent
from src.agents.planner import PlannerAgent
from src.harness.workspace import SprintWorkspace
from src.llm.client import LLMClient
from src.workflow.state import AgentState

from .mocks.llm_responses import (
    EVALUATOR_RESPONSE,
    GENERATOR_FINAL_RESPONSE,
    GENERATOR_TOOL_CALLS,
    PLANNER_RESPONSE,
)


def make_ai_message(content: str, tool_calls: list | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg


@pytest.fixture
def mock_llm_client():
    """Mock LLM client — returns pre-recorded responses, no real API calls."""
    client = MagicMock(spec=LLMClient)
    client.total_tokens_used = 0

    async def mock_chat(messages, tools=None, tool_choice=None):
        if tools:
            tool_calls_for_response = []
            for i, tc in enumerate(GENERATOR_TOOL_CALLS):
                tool_calls_for_response.append({
                    "id": f"call_{i}",
                    "name": tc["tool_name"],
                    "args": tc["args"],
                })
            return make_ai_message(GENERATOR_FINAL_RESPONSE, tool_calls=tool_calls_for_response)

        system_content = ""
        for msg in messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                system_content = msg.content[:200]
                break

        if "You are the Planner" in system_content:
            return make_ai_message(PLANNER_RESPONSE)
        elif "You are the Evaluator" in system_content:
            return make_ai_message(EVALUATOR_RESPONSE)
        else:
            return make_ai_message(EVALUATOR_RESPONSE)

    client.chat = AsyncMock(side_effect=mock_chat)
    client.build_messages = MagicMock(side_effect=lambda sys, user, hist=None: [
        SystemMessage(content=sys),
        HumanMessage(content=user),
    ])
    return client


@pytest.fixture
def toy_repo_path(tmp_path: Path) -> Path:
    """Create a minimal toy repo with enough structure for real tool execution."""
    repo = tmp_path / "toy-repo"
    repo.mkdir()

    (repo / "CONVENTIONS.md").write_text(
        "# Conventions\n\n"
        "- Models: PascalCase, tables: snake_case\n"
        "- Routes: kebab-case\n"
        "- Use pytest fixtures\n"
    )

    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")

    models = src / "models"
    models.mkdir()
    (models / "__init__.py").write_text("")
    (models / "user.py").write_text(
        'from sqlmodel import SQLModel, Field\n'
        'from typing import Optional\n\n'
        'class User(SQLModel, table=True):\n'
        '    __tablename__ = "users"\n'
        '    id: Optional[int] = Field(default=None, primary_key=True)\n'
        '    username: str = Field(max_length=50, unique=True)\n'
        '    email: str = Field(max_length=100)\n'
    )
    (models / "note.py").write_text(
        'from sqlmodel import SQLModel, Field\n'
        'from typing import Optional\n\n'
        'class Note(SQLModel, table=True):\n'
        '    __tablename__ = "notes"\n'
        '    id: Optional[int] = Field(default=None, primary_key=True)\n'
        '    title: str = Field(max_length=100)\n'
        '    content: str\n'
        '    user_id: int = Field(foreign_key="users.id")\n'
    )

    for subdir in ("routers", "services", "schemas"):
        d = src / subdir
        d.mkdir()
        (d / "__init__.py").write_text("")

    (src / "database.py").write_text(
        'from sqlmodel import Session, create_engine, SQLModel\n\n'
        'engine = create_engine("sqlite:///:memory:")\n\n'
        'def get_session():\n'
        '    return Session(engine)\n'
    )

    tests = repo / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text(
        'import pytest\n'
        'from sqlmodel import SQLModel, Session, create_engine\n\n'
        '@pytest.fixture\n'
        'def session():\n'
        '    engine = create_engine("sqlite:///:memory:")\n'
        '    SQLModel.metadata.create_all(engine)\n'
        '    with Session(engine) as s:\n'
        '        yield s\n'
    )

    return repo


@pytest.fixture
def initial_state(toy_repo_path: Path) -> AgentState:
    return {
        "request": "Add a Tag resource following existing patterns, with Note-Tag many-to-many relationship",
        "plan": [],
        "sprint_contract": [],
        "execution_trace": [],
        "code_diff": "",
        "eval_feedback": None,
        "retry_count": 0,
        "task_id": "real-tools-test",
        "codebase_path": str(toy_repo_path),
        "sprint_workspace": "",
        "current_sprint": 1,
        "final_verdict": None,
        "error": None,
    }


class TestRealToolExecution:
    """Generator + SprintWorkspace + Evaluator with real filesystem operations."""

    @pytest.mark.asyncio
    async def test_generator_writes_files_to_sprint_workspace(
        self, mock_llm_client, initial_state
    ):
        """Generator must create real files in the sprint workspace.

        This is the core integration test: LLM returns tool calls,
        write_file actually writes to disk, and the sprint workspace
        contains the generated files.
        """
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        state2 = await generator.execute(state1)

        sprint_ws = Path(state2["sprint_workspace"])
        assert sprint_ws.exists(), "Sprint workspace was not created"

        # Verify the Generator wrote real files into the sprint workspace
        assert (sprint_ws / "src" / "models" / "tag.py").exists(), \
            "tag.py not created in sprint workspace"
        assert (sprint_ws / "src" / "models" / "note_tag.py").exists(), \
            "note_tag.py not created in sprint workspace"
        assert (sprint_ws / "src" / "routers" / "tags.py").exists(), \
            "tags.py not created in sprint workspace"
        assert (sprint_ws / "tests" / "test_tags.py").exists(), \
            "test_tags.py not created in sprint workspace"

        # Verify file contents match what the mock LLM requested
        tag_content = (sprint_ws / "src" / "models" / "tag.py").read_text()
        assert "Tag(SQLModel, table=True)" in tag_content
        assert "name: str" in tag_content

    @pytest.mark.asyncio
    async def test_diff_reflects_actual_changes(
        self, mock_llm_client, initial_state
    ):
        """code_diff must contain the actual unified diff of new files."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        state2 = await generator.execute(state1)

        diff = state2["code_diff"]
        assert diff != "(no changes)", "Diff should not be empty"
        # Use os-native path separator (backslash on Windows)
        assert f"models{os.sep}tag.py" in diff, "Diff should mention tag.py"
        assert "--- /dev/null" in diff, "New files should use --- /dev/null"

    @pytest.mark.asyncio
    async def test_evaluator_sensors_run_on_sprint_workspace(
        self, mock_llm_client, initial_state
    ):
        """Evaluator's run_tests and run_lint must execute in the sprint workspace.

        Sensors are NOT mocked — pytest and ruff actually run against
        the files the Generator wrote.
        """
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        state2 = await generator.execute(state1)

        evaluator = EvaluatorAgent(mock_llm_client)

        with patch("src.agents.evaluator.read_file", return_value="# Conventions"):
            original_run_sensors = evaluator._run_sensors
            sensor_results_captured = {}

            async def capturing_run_sensors(workspace):
                results = await original_run_sensors(workspace)
                sensor_results_captured.update(results)
                return results

            evaluator._run_sensors = capturing_run_sensors
            state3 = await evaluator.execute(state2)

        # Sensor results should reflect real pytest execution
        assert "tests" in sensor_results_captured, "run_tests was not called"
        test_result = sensor_results_captured["tests"]
        assert "passed" in test_result, "Test result missing 'passed' count"
        assert "exit_code" in test_result, "Test result missing 'exit_code'"

        # Evaluator should have updated the contract
        assert "sprint_contract" in state3
        for criterion in state3["sprint_contract"]:
            assert criterion["status"] in ("PASS", "FAIL")

    @pytest.mark.asyncio
    async def test_workspace_merge_writes_back_to_codebase(
        self, mock_llm_client, initial_state, toy_repo_path
    ):
        """After merge(), generated files must appear in the original codebase."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        state2 = await generator.execute(state1)

        sprint_ws = Path(state2["sprint_workspace"])
        assert sprint_ws.exists()

        # Simulate PASS → merge lifecycle
        ws = SprintWorkspace(
            codebase_path=str(toy_repo_path),
            task_id="real-tools-test",
            sprint=1,
        )
        ws.path = sprint_ws
        ws.merge()

        # Files should now be in the original codebase
        assert (toy_repo_path / "src" / "models" / "tag.py").exists(), \
            "tag.py not merged back to codebase"
        assert (toy_repo_path / "src" / "models" / "note_tag.py").exists(), \
            "note_tag.py not merged back to codebase"
        assert (toy_repo_path / "src" / "routers" / "tags.py").exists(), \
            "tags.py not merged back to codebase"
        assert (toy_repo_path / "tests" / "test_tags.py").exists(), \
            "test_tags.py not merged back to codebase"

        # Workspace should be cleaned up after merge
        assert not sprint_ws.exists(), "Sprint workspace should be discarded after merge"
