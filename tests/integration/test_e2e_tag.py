"""End-to-end test for the Tag task.

This test verifies the complete PGE loop:
1. Planner produces a plan and sprint contract
2. Generator implements the code
3. Evaluator verifies the implementation

Uses mock LLM responses to avoid real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.evaluator import EvaluatorAgent
from src.agents.generator import GeneratorAgent
from src.agents.planner import PlannerAgent
from src.llm.client import LLMClient
from src.workflow.graph import run_task
from src.workflow.state import AgentState, Criterion

from .mocks.llm_responses import (
    EVALUATOR_RESPONSE,
    GENERATOR_FINAL_RESPONSE,
    GENERATOR_TOOL_CALLS,
    PLANNER_RESPONSE,
)


def make_ai_message(content: str, tool_calls: list | None = None) -> AIMessage:
    """Create an AIMessage with optional tool calls."""
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client with pre-recorded responses."""
    client = MagicMock(spec=LLMClient)
    client.total_tokens_used = 0

    # Track call order to return appropriate responses
    call_order = {"count": 0}

    async def mock_chat(messages, tools=None, tool_choice=None):
        call_order["count"] += 1

        # Check if this is a tool-calling request (Generator)
        if tools:
            # This is a Generator call - return tool calls
            tool_calls_for_response = []
            for i, tc in enumerate(GENERATOR_TOOL_CALLS):
                tool_calls_for_response.append({
                    "id": f"call_{i}",
                    "name": tc["tool_name"],
                    "args": tc["args"],
                })
            return make_ai_message(GENERATOR_FINAL_RESPONSE, tool_calls=tool_calls_for_response)

        # Check message content to determine agent type
        system_content = ""
        for msg in messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                system_content = msg.content[:200]
                break

        # Detect agent type from system prompt keywords
        if "You are the Planner" in system_content:
            return make_ai_message(PLANNER_RESPONSE)
        elif "You are the Evaluator" in system_content:
            return make_ai_message(EVALUATOR_RESPONSE)
        else:
            # Fallback: use call order (1=planner, 2+=evaluator)
            if call_order["count"] == 1:
                return make_ai_message(PLANNER_RESPONSE)
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
        "sprint_workspace": "",
        "current_sprint": 1,
        "final_verdict": None,
        "error": None,
    }


class TestPlannerAgent:
    """Test Planner agent produces correct plan and contract."""

    @pytest.mark.asyncio
    async def test_planner_produces_plan(self, mock_llm_client, initial_state):
        """Planner should produce a structured plan."""
        planner = PlannerAgent(mock_llm_client)

        # Mock the tool execution for read_file
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            result = await planner.execute(initial_state)

        assert "plan" in result
        assert len(result["plan"]) > 0

        # Verify plan structure
        for step in result["plan"]:
            assert "id" in step
            assert "description" in step
            assert "dependencies" in step

    @pytest.mark.asyncio
    async def test_planner_produces_contract(self, mock_llm_client, initial_state):
        """Planner should produce a sprint contract with all criteria starting as FAIL."""
        planner = PlannerAgent(mock_llm_client)

        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            result = await planner.execute(initial_state)

        assert "sprint_contract" in result
        assert len(result["sprint_contract"]) > 0

        # Verify all criteria start as FAIL
        for criterion in result["sprint_contract"]:
            assert criterion["status"] == "FAIL"
            assert criterion["evidence"] == ""

    @pytest.mark.asyncio
    async def test_planner_sets_current_sprint(self, mock_llm_client, initial_state):
        """Planner should set current_sprint to 1."""
        planner = PlannerAgent(mock_llm_client)

        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            result = await planner.execute(initial_state)

        assert result["current_sprint"] == 1


class TestGeneratorAgent:
    """Test Generator agent implements code correctly."""

    @pytest.mark.asyncio
    async def test_generator_produces_code_diff(self, mock_llm_client, initial_state, toy_repo_path):
        """Generator should produce a code diff."""
        # First run planner to get contract
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state_after_planner = await planner.execute(initial_state)

        # Now run generator
        generator = GeneratorAgent(mock_llm_client)

        # Mock tool execution
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            result = await generator.execute(state_after_planner)

        assert "code_diff" in result
        assert result["code_diff"] != ""

    @pytest.mark.asyncio
    async def test_generator_records_execution_trace(self, mock_llm_client, initial_state, toy_repo_path):
        """Generator should record all tool calls in execution trace."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state_after_planner = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)

        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            result = await generator.execute(state_after_planner)

        assert "execution_trace" in result
        assert len(result["execution_trace"]) > 0

        # Verify trace structure
        for entry in result["execution_trace"]:
            assert "tool_name" in entry
            assert "args" in entry
            assert "result" in entry


class TestEvaluatorAgent:
    """Test Evaluator agent evaluates correctly."""

    @pytest.mark.asyncio
    async def test_evaluator_updates_contract(self, mock_llm_client, initial_state, toy_repo_path):
        """Evaluator should update contract criteria status."""
        # Run planner first
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state_after_planner = await planner.execute(initial_state)

        # Run generator
        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state_after_generator = await generator.execute(state_after_planner)

        # Run evaluator
        evaluator = EvaluatorAgent(mock_llm_client)

        # Mock sensor execution (tests + lint)
        with patch("src.agents.evaluator.run_tests") as mock_tests, \
             patch("src.agents.evaluator.run_lint") as mock_lint, \
             patch("src.agents.evaluator.read_file", return_value="# Conventions"):

            mock_tests.return_value = {"passed": 3, "failed": 0}
            mock_lint.return_value = {"issue_count": 0}

            result = await evaluator.execute(state_after_generator)

        assert "sprint_contract" in result

        # Verify criteria were updated
        for criterion in result["sprint_contract"]:
            assert criterion["status"] in ["PASS", "FAIL"]

    @pytest.mark.asyncio
    async def test_evaluator_produces_verdict(self, mock_llm_client, initial_state, toy_repo_path):
        """Evaluator should produce a sprint-level verdict."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state_after_planner = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state_after_generator = await generator.execute(state_after_planner)

        evaluator = EvaluatorAgent(mock_llm_client)

        with patch("src.agents.evaluator.run_tests") as mock_tests, \
             patch("src.agents.evaluator.run_lint") as mock_lint, \
             patch("src.agents.evaluator.read_file", return_value="# Conventions"):

            mock_tests.return_value = {"passed": 3, "failed": 0}
            mock_lint.return_value = {"issue_count": 0}

            result = await evaluator.execute(state_after_generator)

        assert "final_verdict" in result
        assert result["final_verdict"] in ["PASS", "PASS_WITH_WARNINGS", "FAIL"]

    @pytest.mark.asyncio
    async def test_evaluator_fresh_context_isolation(self, mock_llm_client, initial_state, toy_repo_path):
        """Evaluator should NOT see execution_trace (fresh context isolation)."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state_after_planner = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state_after_generator = await generator.execute(state_after_planner)

        # Verify execution_trace exists in state
        assert len(state_after_generator.get("execution_trace", [])) > 0

        # Evaluator should only see code_diff + sprint_contract
        evaluator = EvaluatorAgent(mock_llm_client)

        with patch("src.agents.evaluator.run_tests") as mock_tests, \
             patch("src.agents.evaluator.run_lint") as mock_lint, \
             patch("src.agents.evaluator.read_file", return_value="# Conventions"):

            mock_tests.return_value = {"passed": 3, "failed": 0}
            mock_lint.return_value = {"issue_count": 0}

            # The evaluator internally uses FreshContextEvaluator to isolate
            result = await evaluator.execute(state_after_generator)

        # Result should have updated contract
        assert "sprint_contract" in result


class TestFullPGEFlow:
    """Test the complete PGE loop."""

    @pytest.mark.asyncio
    async def test_all_criteria_pass(self, mock_llm_client, initial_state, toy_repo_path):
        """All sprint contract criteria should pass after evaluation."""
        # Run full flow manually (not through graph, to avoid real LLM calls)
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state2 = await generator.execute(state1)

        evaluator = EvaluatorAgent(mock_llm_client)
        with patch("src.agents.evaluator.run_tests") as mock_tests, \
             patch("src.agents.evaluator.run_lint") as mock_lint, \
             patch("src.agents.evaluator.read_file", return_value="# Conventions"):

            mock_tests.return_value = {"passed": 3, "failed": 0}
            mock_lint.return_value = {"issue_count": 0}

            final_state = await evaluator.execute(state2)

        # Verify all criteria passed
        for criterion in final_state["sprint_contract"]:
            assert criterion["status"] == "PASS", f"Criterion {criterion['id']} failed: {criterion.get('evidence', '')}"

    @pytest.mark.asyncio
    async def test_state_flows_correctly(self, mock_llm_client, initial_state, toy_repo_path):
        """State should flow correctly through all agents."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        # Verify planner added its output
        assert len(state1["plan"]) > 0
        assert len(state1["sprint_contract"]) > 0
        assert state1["current_sprint"] == 1

        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state2 = await generator.execute(state1)

        # Verify generator added its output
        assert state2["code_diff"] != ""
        assert len(state2["execution_trace"]) > 0

        # Verify planner output preserved
        assert state2["plan"] == state1["plan"]
        assert state2["sprint_contract"] == state1["sprint_contract"]

    @pytest.mark.asyncio
    async def test_contract_default_fail(self, mock_llm_client, initial_state, toy_repo_path):
        """All contract criteria should start as FAIL (Default-FAIL principle)."""
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state = await planner.execute(initial_state)

        for criterion in state["sprint_contract"]:
            assert criterion["status"] == "FAIL", "Criteria must start as FAIL (Default-FAIL principle)"


@pytest.mark.integration
class TestE2ETagTask:
    """Full E2E test for the Tag task (marked as integration)."""

    @pytest.mark.asyncio
    async def test_tag_task_e2e(self, mock_llm_client, toy_repo_path):
        """Complete E2E test: request → plan → generate → evaluate → all PASS."""
        initial_state: AgentState = {
            "request": "Add a Tag resource following existing patterns, with Note-Tag many-to-many relationship",
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "e2e-test-001",
            "codebase_path": str(toy_repo_path),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        # Run full PGE flow
        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state1 = await planner.execute(initial_state)

        generator = GeneratorAgent(mock_llm_client)
        with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Tool executed successfully"
            state2 = await generator.execute(state1)

        evaluator = EvaluatorAgent(mock_llm_client)
        with patch("src.agents.evaluator.run_tests") as mock_tests, \
             patch("src.agents.evaluator.run_lint") as mock_lint, \
             patch("src.agents.evaluator.read_file", return_value="# Conventions"):

            mock_tests.return_value = {"passed": 3, "failed": 0}
            mock_lint.return_value = {"issue_count": 0}

            final_state = await evaluator.execute(state2)

        # Assertions
        assert final_state["error"] is None, f"Task failed with error: {final_state.get('error')}"

        # All criteria should pass
        failed_criteria = [c for c in final_state["sprint_contract"] if c["status"] == "FAIL"]
        assert len(failed_criteria) == 0, f"Failed criteria: {failed_criteria}"

        # Should have a verdict
        assert final_state["final_verdict"] in ["PASS", "PASS_WITH_WARNINGS"]

        # Should have execution trace
        assert len(final_state["execution_trace"]) > 0

        # Should have code diff
        assert final_state["code_diff"] != ""

    @pytest.mark.asyncio
    async def test_tag_task_produces_valid_plan(self, mock_llm_client, toy_repo_path):
        """Planner should produce a valid plan with steps and dependencies."""
        initial_state: AgentState = {
            "request": "Add a Tag resource following existing patterns",
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "e2e-test-002",
            "codebase_path": str(toy_repo_path),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state = await planner.execute(initial_state)

        # Verify plan structure
        assert len(state["plan"]) > 0
        for step in state["plan"]:
            assert "id" in step
            assert "description" in step
            assert isinstance(step["dependencies"], list)

    @pytest.mark.asyncio
    async def test_tag_task_contract_has_criteria(self, mock_llm_client, toy_repo_path):
        """Sprint contract should have meaningful criteria."""
        initial_state: AgentState = {
            "request": "Add a Tag resource following existing patterns",
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "e2e-test-003",
            "codebase_path": str(toy_repo_path),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        planner = PlannerAgent(mock_llm_client)
        with patch("src.agents.planner.read_file", return_value="# Conventions"):
            state = await planner.execute(initial_state)

        # Verify contract structure
        assert len(state["sprint_contract"]) > 0
        for criterion in state["sprint_contract"]:
            assert "id" in criterion
            assert "description" in criterion
            assert criterion["status"] == "FAIL"
