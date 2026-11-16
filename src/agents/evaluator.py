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


def assess_sensors(sensor_results: dict) -> tuple[bool, str]:
    """Return whether pytest and ruff both ran cleanly, plus a short explanation."""
    if sensor_results.get("error") and "tests" not in sensor_results:
        return False, f"Sensors failed to run: {sensor_results['error']}"

    notes: list[str] = []
    ok = True

    tests = sensor_results.get("tests")
    if not isinstance(tests, dict):
        ok = False
        notes.append("Tests did not run.")
    else:
        exit_code = tests.get("exit_code", 1)
        failed = int(tests.get("failed", 0) or 0)
        if exit_code != 0 or failed:
            ok = False
            notes.append(
                f"Tests failed (exit={exit_code}, passed={tests.get('passed', 0)}, failed={failed})."
            )
            output = tests.get("output") or ""
            if output:
                notes.append(str(output)[-1500:])

    lint = sensor_results.get("lint")
    if not isinstance(lint, dict):
        ok = False
        notes.append("Lint did not run.")
    elif lint.get("exit_code", 1) != 0:
        ok = False
        notes.append(f"Lint failed (exit={lint.get('exit_code')}, issues={lint.get('issue_count', 0)}).")

    return ok, "\n".join(notes)


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

        sensors_passed, sensor_detail = assess_sensors(sensor_results)

        # Append sensor results to user message. Exit codes are included so a
        # collection error (0 passed, 0 failed, non-zero exit) is visible.
        sensor_info = ""
        if "tests" in sensor_results:
            test_res = sensor_results["tests"]
            sensor_info += (
                "\n\n## Independent Test Results\n"
                f"Passed: {test_res.get('passed', 0)}, Failed: {test_res.get('failed', 0)}, "
                f"Exit code: {test_res.get('exit_code', 'missing')}"
            )
        if "lint" in sensor_results:
            lint_res = sensor_results["lint"]
            sensor_info += (
                "\n\n## Independent Lint Results\n"
                f"Issues: {lint_res.get('issue_count', 0)}, Exit code: {lint_res.get('exit_code', 'missing')}"
            )
        if sensor_results.get("error"):
            sensor_info += f"\n\n## Sensor Error\n{sensor_results['error']}"
        if not sensors_passed and sensor_detail:
            sensor_info += f"\n\n## Sensor Gate\n{sensor_detail}"

        user += sensor_info
        messages = self._build_messages(system, user)

        # Call LLM for reasoning sensors (regular chat + JSON parsing)
        response = await self.record_cost(messages, state, role="evaluator")
        content = response.content if isinstance(response.content, str) else str(response.content)
        result = extract_json(content)

        # Update contract statuses
        updated_contract = []
        for criterion in contract:
            c = Criterion(**criterion)
            for eval_criterion in result.get("criteria_results", []):
                if eval_criterion.get("id") == c.id:
                    raw_status = eval_criterion.get("status", "FAIL")
                    c.status = raw_status if raw_status in ("PASS", "FAIL") else "FAIL"
                    c.evidence = eval_criterion.get("evidence", "")
                    break
            updated_contract.append(c.model_dump())

        # Build eval feedback for Generator (only if FAIL)
        blocking = result.get("blocking_issues", [])
        feedback = result.get("feedback", "")
        eval_feedback = None
        has_fail = any(c["status"] == "FAIL" for c in updated_contract) or not sensors_passed
        if has_fail:
            passed = [c for c in updated_contract if c["status"] == "PASS"]
            parts = []
            if passed:
                parts.append("Already passing (DO NOT break these):\n" + json.dumps(passed, indent=2))
            parts.append("Issues found:\n" + json.dumps(blocking, indent=2))
            parts.append("Feedback:\n" + feedback)
            if not sensors_passed:
                parts.append("Computational sensors failed:\n" + sensor_detail)
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
            "sensors_passed": sensors_passed,
            "error": None,
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
