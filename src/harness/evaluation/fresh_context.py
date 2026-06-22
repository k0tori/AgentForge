from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EvalInput:
    """Isolated input for the Evaluator.

    This is the enforcement mechanism for Fresh-context isolation (section 4.3).
    ONLY code_diff and sprint_contract are passed to the Evaluator.
    execution_trace, Generator's intermediate attempts, etc. are explicitly excluded.
    """

    code_diff: str
    sprint_contract: list[dict]


class FreshContextEvaluator:
    """Wraps Evaluator calls with explicit state trimming.

    The key invariant: the Evaluator NEVER sees the full AgentState.
    This class enforces that by construction, not by convention.
    """

    @staticmethod
    def build_eval_input(code_diff: str, sprint_contract: list[dict]) -> EvalInput:
        """Construct a trimmed input containing ONLY what the Evaluator should see."""
        return EvalInput(code_diff=code_diff, sprint_contract=sprint_contract)

    @staticmethod
    def validate_no_leakage(eval_input: EvalInput, full_state: dict) -> bool:
        """Verify that eval_input doesn't contain fields from full_state that should be hidden.

        This is a safety check for testing — not called in production flow.
        """
        forbidden_keys = {"execution_trace", "eval_feedback", "retry_count"}
        input_dict = eval_input.__dict__
        for key in forbidden_keys:
            if key in input_dict:
                return False
        return True
