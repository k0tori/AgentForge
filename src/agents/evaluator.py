from __future__ import annotations

import json
import re

from src.agents.base import BaseAgent
from src.agents.prompts.evaluator import EVALUATOR_SYSTEM_PROMPT, EVALUATOR_USER_PROMPT
from src.harness.evaluation.fresh_context import FreshContextEvaluator
from src.harness.evaluation.rubric import compute_verdict
from src.tools.file_ops import read_file
from src.tools.test_ops import run_lint, run_tests
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

        # Run computational sensors independently
        sensor_results = await self._run_sensors(codebase_path)

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
        result = self._extract_json(content)

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
            eval_feedback = f"Issues found:\n{json.dumps(blocking, indent=2)}\n\nFeedback:\n{feedback}"

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

    async def _run_sensors(self, codebase_path: str) -> dict:
        """Run computational sensors (tests + lint) independently.

        Args:
            codebase_path: Path to the codebase. Also used as the allowed workspace
                boundary — subprocess will refuse to run outside this directory.
        """
        results = {}
        try:
            results["tests"] = run_tests(codebase_path, allowed_workspace=codebase_path)
            results["lint"] = run_lint(codebase_path, allowed_workspace=codebase_path)
        except Exception as e:
            results["error"] = str(e)
        return results

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON object from LLM response text.

        Handles common LLM JSON errors:
        - JSON in markdown code fences
        - Missing commas between properties
        - Trailing commas
        """
        # Try markdown code fence first
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # Try to repair common issues
                repaired = EvaluatorAgent._repair_json(json_str)
                return json.loads(repaired)

        # Find raw JSON object using bracket counting
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in response: {text[:200]}")

        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    json_str = text[start : i + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        # Try to repair common issues
                        repaired = EvaluatorAgent._repair_json(json_str)
                        return json.loads(repaired)

        raise ValueError(f"Could not extract JSON from response: {text[:200]}")

    @staticmethod
    def _repair_json(json_str: str) -> str:
        """Attempt to repair common JSON formatting issues from LLM output.

        Fixes:
        - Missing commas between object properties
        - Trailing commas before } or ]
        """
        # Fix trailing commas: ,} → } and ,] → ]
        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)

        # Fix missing commas between properties:
        # Pattern: "value" followed by newline and whitespace then "key"
        json_str = re.sub(
            r'("(?:[^"\\]|\\.)*")\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)',
            r'\1,\n\2',
            json_str,
        )

        # Fix missing commas after closing } or ] before next property
        json_str = re.sub(r'([}\]])\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)', r'\1,\n\2', json_str)

        # Fix missing commas after values (number, boolean, null) before next property
        json_str = re.sub(
            r'(true|false|null|\d+(?:\.\d+)?)\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)',
            r'\1,\n\2',
            json_str,
        )

        return json_str
