"""Helpers for splitting a contract across sprints."""
from __future__ import annotations

from src.workflow.state import AgentState


def active_criteria(state: AgentState) -> list[dict]:
    """Criteria the current sprint must satisfy.

    A contract with no later sprint_id is one sprint: every criterion is active.
    """
    contract = list(state.get("sprint_contract") or [])
    current = int(state.get("current_sprint") or 1)
    if not any(int(c.get("sprint_id") or current) != current for c in contract):
        return contract
    return [c for c in contract if int(c.get("sprint_id") or current) == current]


def next_sprint_id(state: AgentState) -> int | None:
    """The next sprint that still has its own criteria, if the planner split them."""
    current = int(state.get("current_sprint") or 1)
    future = sorted({
        int(c["sprint_id"])
        for c in state.get("sprint_contract") or []
        if c.get("sprint_id") is not None and int(c["sprint_id"]) > current
    })
    return future[0] if future else None
