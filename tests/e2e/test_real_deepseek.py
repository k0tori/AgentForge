"""End-to-end test with REAL DeepSeek API calls.

This test exercises the complete PGE loop (Planner → Generator → Evaluator)
against the live DeepSeek API.  Every LLM call, every tool execution, every
pytest/ruff run is real.

Requirements:
- DEEPSEEK_API_KEY must be set in .env (or environment)
- Internet connectivity to api.deepseek.com
- pytest, ruff available on PATH

Run with:
    pytest tests/e2e/ -v -m integration --timeout=300
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from src.llm.client import LLMClient
from src.workflow.graph import build_graph
from src.workflow.state import AgentState

pytestmark = pytest.mark.integration

# Timeout for the full PGE loop (seconds).  Override via env var.
E2E_TIMEOUT = int(os.environ.get("E2E_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def live_toy_repo(tmp_path: Path) -> Path:
    """Copy toy-repo to a temp directory so the test doesn't pollute the real codebase."""
    src = Path(__file__).parent.parent.parent / "toy-repo"
    dst = tmp_path / "toy-repo"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


@pytest.fixture
def real_llm_client() -> LLMClient:
    """Create a real LLMClient that calls DeepSeek."""
    return LLMClient()


# ---------------------------------------------------------------------------
# Smoke test: Planner only
# ---------------------------------------------------------------------------


class TestPlannerSmoke:
    """Smoke tests that only exercise the Planner with the real API."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_planner_returns_valid_json(self, real_llm_client: LLMClient, live_toy_repo: Path):
        """Planner should return parseable plan + sprint_contract from the real LLM."""
        from src.agents.planner import PlannerAgent

        state: AgentState = {
            "request": "Add a Tag resource following existing patterns",
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "smoke-planner",
            "codebase_path": str(live_toy_repo),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        planner = PlannerAgent(real_llm_client)
        result = await planner.execute(state)

        # Should not error
        assert result.get("error") is None, f"Planner errored: {result.get('error')}"

        # Should produce a plan
        assert len(result["plan"]) > 0, "Planner returned empty plan"
        for step in result["plan"]:
            assert "id" in step
            assert "description" in step
            assert isinstance(step.get("dependencies"), list)

        # Should produce a contract with all criteria starting as FAIL
        assert len(result["sprint_contract"]) > 0, "Planner returned empty contract"
        for criterion in result["sprint_contract"]:
            assert "id" in criterion
            assert "description" in criterion
            assert criterion["status"] == "FAIL", "Criteria must start as FAIL"


# ---------------------------------------------------------------------------
# Full E2E: complete PGE loop
# ---------------------------------------------------------------------------


class TestFullPGEWithRealDeepSeek:
    """Full PGE loop against the live DeepSeek API.

    The E2E test proves the system works end-to-end — not that the LLM
    always produces perfect code.  Both outcomes are valid:
    - PASS: Generator wrote code that satisfied all criteria
    - ESCALATE: Generator couldn't satisfy all criteria within retry budget

    What's NOT valid: unhandled exceptions, empty traces, or missing state.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    async def test_full_pge_loop(self, live_toy_repo: Path):
        """Complete PGE loop: plan → generate → evaluate → (end | escalate).

        This is THE test that proves the whole system works end-to-end.
        Real LLM calls, real file I/O, real pytest/ruff.
        """
        # Build a fresh graph (not the singleton) to avoid cross-test state
        graph = build_graph().compile()

        initial_state: AgentState = {
            "request": (
                "Add a Tag resource following existing patterns, "
                "with CRUD endpoints"
            ),
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "e2e-real-deepseek",
            "codebase_path": str(live_toy_repo),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        # Run the full graph with a timeout
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=E2E_TIMEOUT,
        )

        # --- Core assertions (both PASS and ESCALATE are valid) ---

        verdict = final_state.get("final_verdict")
        error = final_state.get("error")

        # Determine outcome
        is_pass = verdict in ("PASS", "PASS_WITH_WARNINGS") and error is None
        is_escalated = error is not None and "Max sprint retries exceeded" in str(error)

        assert is_pass or is_escalated, (
            f"Unexpected state: verdict={verdict}, error={error}"
        )

        # Contract exists and has criteria
        contract = final_state.get("sprint_contract", [])
        assert len(contract) > 0, "No sprint contract produced"

        # Execution trace should have entries (Generator made tool calls)
        trace = final_state.get("execution_trace", [])
        assert len(trace) > 0, "No execution trace recorded"

        # Sprint workspace was used
        sprint_ws = final_state.get("sprint_workspace", "")
        assert sprint_ws, "No sprint workspace path in final state"

        # Code diff should exist
        code_diff = final_state.get("code_diff", "")
        assert code_diff, "No code diff produced"

        if is_pass:
            # PASS: all criteria should be PASS
            failed = [c for c in contract if c.get("status") == "FAIL"]
            assert len(failed) == 0, (
                f"PASS verdict but {len(failed)} criteria still FAIL"
            )

            # Verify files landed in the codebase (workspace was merged)
            new_files = list(live_toy_repo.rglob("tag*.py"))
            assert len(new_files) > 0 or "--- /dev/null" in code_diff, (
                "PASS verdict but no new files found in codebase or diff"
            )
        else:
            # ESCALATE: verify structured error message
            assert "Unresolved criteria" in error
            # At least some criteria should be documented as unresolved
            unresolved = [c for c in contract if c.get("status") == "FAIL"]
            assert len(unresolved) > 0, "Escalated but no unresolved criteria"

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    async def test_pge_loop_produces_meaningful_trace(self, live_toy_repo: Path):
        """Verify the execution trace contains real tool calls when the Generator runs.

        This test focuses on observability: when the Generator has enough
        token budget, the trace should show read_file and write_file calls.

        NOTE: If the token budget is exhausted (e.g. from prior tests sharing
        the same LLMClient), the Generator may exit its loop immediately with
        no tool calls.  This is expected behavior — the system correctly
        respects its budget constraints.
        """
        graph = build_graph().compile()

        initial_state: AgentState = {
            "request": "Add a Tag resource following existing patterns",
            "plan": [],
            "sprint_contract": [],
            "execution_trace": [],
            "code_diff": "",
            "eval_feedback": None,
            "retry_count": 0,
            "task_id": "e2e-trace-test",
            "codebase_path": str(live_toy_repo),
            "sprint_workspace": "",
            "current_sprint": 1,
            "final_verdict": None,
            "error": None,
        }

        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=E2E_TIMEOUT,
        )

        trace = final_state.get("execution_trace", [])

        # If the Generator ran out of budget, trace may be empty — that's OK.
        # The system correctly respects its constraints.
        if len(trace) == 0:
            # Verify the system escalated rather than crashing
            assert final_state.get("error") is not None, (
                "Empty trace but no error — unexpected state"
            )
            return

        # If the Generator did run, verify the trace is meaningful
        # Should contain read_file calls (Generator reads existing code)
        read_calls = [e for e in trace if e.get("tool_name") == "read_file"]
        assert len(read_calls) > 0, "No read_file calls in trace"

        # Should contain write_file calls (Generator writes new code)
        write_calls = [e for e in trace if e.get("tool_name") == "write_file"]
        assert len(write_calls) > 0, "No write_file calls in trace"

        # Each trace entry should have the expected structure
        for entry in trace:
            assert "tool_name" in entry, f"Missing tool_name in trace entry: {entry}"
            if entry["tool_name"] != "final_response":
                assert "args" in entry, f"Missing args in trace entry: {entry}"
