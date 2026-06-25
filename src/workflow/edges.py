from __future__ import annotations

from src.config import settings
from src.workflow.state import AgentState


def route_after_evaluate(state: AgentState) -> str:
    """Route after evaluation based on Contract-level binary status.

    This is the ONLY routing function in the StateGraph.
    It uses Contract-level FAIL/PASS, NOT Sprint-level weighted verdict.

    Returns:
        "end" if ALL criteria PASS
        "generate" if any FAIL and retries remaining
        "escalate" if any FAIL and max retries exceeded
    """
    contract = state.get("sprint_contract", [])
    retry_count = state.get("retry_count", 0)

    # Check if ALL criteria are PASS
    all_pass = all(c.get("status") == "PASS" for c in contract)
    if all_pass:
        return "end"

    # Check retry budget (using config directly instead of instantiating LoopController)
    if retry_count < settings.MAX_SPRINT_RETRIES:
        return "generate"

    # Max retries exceeded
    return "escalate"


def handle_escalation(state: AgentState) -> AgentState:
    """Handle escalation when max retries are exceeded.

    Produces a structured failure report with specific unresolved items.
    """
    contract = state.get("sprint_contract", [])
    unresolved = [
        c for c in contract if c.get("status") == "FAIL"
    ]

    error_msg = "Max sprint retries exceeded. Unresolved criteria:\n"
    for c in unresolved:
        error_msg += f"  - [{c.get('id')}] {c.get('description')}: {c.get('evidence', 'No evidence')}\n"

    return {
        **state,
        "error": error_msg,
        "final_verdict": "FAIL",
    }
