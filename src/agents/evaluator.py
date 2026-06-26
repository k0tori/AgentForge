from __future__ import annotations

import json

from src.agents.base import BaseAgent
from src.agents.prompts.evaluator import EVALUATOR_SYSTEM_PROMPT, EVALUATOR_USER_PROMPT
from src.harness.evaluation.fresh_context import FreshContextEvaluator
from src.harness.evaluation.rubric import compute_verdict
from src.tools.file_ops import read_file
from src.tools.test_ops import run_lint, run_tests
from src.utils.json_extract import extract_json
from src.workflow.state import AgentState, Criterion


class EvaluatorAgent(BaseAgent):
    """Evaluator: independently evaluates code against the sprint contract.

    CRITICAL: This agent sees ONLY code_diff + sprint_contract.
    It NEVER sees execution_trace or Generator's intermediate attempts.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fresh_context = FreshContextEvaluator()

    async def execute(self, state: AgentState) -> AgentState:
        """Evaluate code against sprint contract with fresh context isolation."""
        code_diff = state.get("code_diff", "")
        contract = state.get("sprint_contract", [])
        codebase_path = state.get("codebase_path", "./toy-repo")

        # CRITICAL: Build isolated input — only code_diff + sprint_contract
        eval_input = self.fresh_context.build_eval_input(code_diff, contract)

        # Read conventions
        conventions_path = f"{codebase_path}/CONVENTIONS.md"
        try:
            conventions = read_file(conventions_path)
        except FileNotFoundError:
            conventions = "No conventions found."

        # Build prompt with ONLY the isolated input
        contract_text = json.dumps(eval_input.sprint_contract, indent=2)
        system = EVALUATOR_SYSTEM_PROMPT.format(conventions=self._truncate(conventions))
        user = EVALUATOR_USER_PROMPT.format(
            code_diff=self._truncate(eval_input.code_diff),
            sprint_contract=contract_text,
        )

        # Run computational sensors independently — test the sprint workspace,
        # not the original codebase, so results reflect Generator's actual changes.
        sprint_workspace = state.get("sprint_workspace", codebase_path)
        sensor_results = await self._run_sensors(sprint_workspace)

        # Append sensor results to user message
        sensor_info = ""
        if "tests" in sensor_results:
            test_res = sensor_results["tests"]
            passed = test_res.get("passed", 0)
            failed = test_res.get("failed", 0)
            sensor_info += f"\n\n## Independent Test Results\nPassed: {passed}, Failed: {failed}"
        if "lint" in sensor_results:
            lint_res = sensor_results["lint"]
            sensor_info += f"\n\n## Independent Lint Results\nIssues: {lint_res.get('issue_count', 0)}"

        user += sensor_info
        messages = self._build_messages(system, user)

        # Call LLM for reasoning sensors (regular chat + JSON parsing)
        response = await self.llm.chat(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        result = extract_json(content)

        # Update contract statuses
        updated_contract = []
        for criterion in contract:
            c = Criterion(**criterion)
            for eval_criterion in result.get("criteria_results", []):
                if eval_criterion.get("id") == c.id:
                    c.status = eval_criterion.get("status", "FAIL")
                    c.evidence = eval_criterion.get("evidence", "")
                    break
            updated_contract.append(c.model_dump())

        # Build eval feedback for Generator (only if FAIL)
        blocking = result.get("blocking_issues", [])
        feedback = result.get("feedback", "")
        eval_feedback = None
        has_fail = any(c["status"] == "FAIL" for c in updated_contract)
        if has_fail:
            passed = [c for c in updated_contract if c["status"] == "PASS"]
            parts = []
            if passed:
                parts.append("Already passing (DO NOT break these):\n" + json.dumps(passed, indent=2))
            parts.append("Issues found:\n" + json.dumps(blocking, indent=2))
            parts.append("Feedback:\n" + feedback)
            eval_feedback = "\n\n".join(parts)

        # Compute sprint-level verdict (for report only, not routing)
        dimension_scores = result.get("dimension_scores", {})
        sprint_verdict = compute_verdict(dimension_scores)

        # Increment retry_count if any criteria failed
        retry_count = state.get("retry_count", 0)
        if has_fail:
            retry_count += 1

        return {
            **state,
            "sprint_contract": updated_contract,
            "eval_feedback": eval_feedback,
            "final_verdict": sprint_verdict,
            "retry_count": retry_count,
        }

    async def _run_sensors(self, sprint_workspace: str) -> dict:
        """Run computational sensors (tests + lint) independently.

        Args:
            sprint_workspace: Path to the sprint workspace directory where
                Generator wrote its code. This is the directory being tested.
        """
        results = {}
        try:
            results["tests"] = run_tests(sprint_workspace, allowed_workspace=sprint_workspace)
            results["lint"] = run_lint(sprint_workspace, allowed_workspace=sprint_workspace)
        except Exception as e:
            results["error"] = str(e)
        return results
