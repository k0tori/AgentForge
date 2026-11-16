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
    if state.get("error"):
        return "escalate"

    contract = state.get("sprint_contract", [])
    retry_count = state.get("retry_count", 0)

    # An empty contract has nothing to prove. all([]) is True, which would merge
    # unreviewed code, so treat it as escalation instead.
    if not contract:
        return "escalate"

    all_pass = all(c.get("status") == "PASS" for c in contract)
    sensors_passed = state.get("sensors_passed", True)
    if all_pass and sensors_passed:
        return "end"

    if retry_count < settings.MAX_SPRINT_RETRIES:
        return "generate"

    return "escalate"


def handle_escalation(state: AgentState) -> AgentState:
    """Handle escalation when max retries are exceeded.

    Produces a structured failure report with specific unresolved items.
    """
    contract = state.get("sprint_contract", [])
    if not contract:
        error_msg = "Sprint contract is empty, so the run cannot pass."
    else:
        unresolved = [c for c in contract if c.get("status") != "PASS"]
        error_msg = "Max sprint retries exceeded. Unresolved criteria:\n"
        for c in unresolved:
            error_msg += f"  - [{c.get('id')}] {c.get('description')}: {c.get('evidence', 'No evidence')}\n"
    if state.get("sensors_passed") is False:
        error_msg += "\nComputational sensors did not pass."
    existing = state.get("error")
    if existing and existing not in error_msg:
        error_msg = f"{existing}\n{error_msg}"

    return {
        **state,
        "error": error_msg,
        "final_verdict": "FAIL",
    }
