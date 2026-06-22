from __future__ import annotations

import json
import time

from src.agents.base import BaseAgent
from src.agents.prompts.evaluator import EVALUATOR_SYSTEM_PROMPT, EVALUATOR_USER_PROMPT
from src.harness.evaluation.fresh_context import FreshContextEvaluator
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
            from src.tools.file_ops import read_file
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

        messages = self._build_messages(system, user)

        # Run computational sensors independently
        sensor_results = await self._run_sensors(codebase_path)

        # Call LLM for reasoning sensors
        from pydantic import BaseModel

        class EvalOutput(BaseModel):
            criteria_results: list[dict]
            sprint_verdict: str
            dimension_scores: dict[str, float]
            blocking_issues: list[str]
            feedback: str

        result = await self.llm.chat_structured(messages, EvalOutput)

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
        if any(c["status"] == "FAIL" for c in updated_contract):
            eval_feedback = f"Issues found:\n{json.dumps(blocking, indent=2)}\n\nFeedback:\n{feedback}"

        # Compute sprint-level verdict (for report only, not routing)
        from src.harness.evaluation.rubric import compute_verdict
        dimension_scores = result.get("dimension_scores", {})
        sprint_verdict = compute_verdict(dimension_scores)

        return {
            **state,
            "sprint_contract": updated_contract,
            "eval_feedback": eval_feedback,
            "final_verdict": sprint_verdict,
        }

    async def _run_sensors(self, codebase_path: str) -> dict:
        """Run computational sensors (tests + lint) independently."""
        results = {}
        try:
            from src.tools.test_ops import run_tests, run_lint
            results["tests"] = run_tests(codebase_path)
            results["lint"] = run_lint(codebase_path)
        except Exception as e:
            results["error"] = str(e)
        return results
