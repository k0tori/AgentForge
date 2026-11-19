from __future__ import annotations

from src.workflow.edges import handle_escalation, handle_next_sprint, route_after_evaluate


def test_all_pass_routes_to_end():
    """When all criteria PASS, route to end."""
    state = {
        "sprint_contract": [
            {"id": "c1", "status": "PASS"},
            {"id": "c2", "status": "PASS"},
        ],
        "retry_count": 0,
    }
    assert route_after_evaluate(state) == "end"


def test_fail_with_retries_routes_to_generate():
    """When criteria FAIL and retries remain, route to generate."""
    state = {
        "sprint_contract": [
            {"id": "c1", "status": "PASS"},
            {"id": "c2", "status": "FAIL"},
        ],
        "retry_count": 1,
    }
    assert route_after_evaluate(state) == "generate"


def test_fail_max_retries_routes_to_escalate():
    """When criteria FAIL and max retries exceeded, route to escalate."""
    state = {
        "sprint_contract": [
            {"id": "c1", "status": "FAIL"},
        ],
        "retry_count": 3,
    }
    assert route_after_evaluate(state) == "escalate"


def test_passing_sprint_advances_when_later_criteria_exist():
    """A passing sprint with later sprint_id criteria moves forward instead of publishing."""
    state = {
        "sprint_contract": [
            {"id": "c1", "sprint_id": 1, "status": "PASS"},
            {"id": "c2", "sprint_id": 2, "status": "FAIL"},
        ],
        "current_sprint": 1,
        "retry_count": 0,
        "sensors_passed": True,
    }
    assert route_after_evaluate(state) == "next_sprint"


def test_last_sprint_routes_to_end():
    state = {
        "sprint_contract": [
            {"id": "c1", "sprint_id": 1, "status": "PASS"},
            {"id": "c2", "sprint_id": 2, "status": "PASS"},
        ],
        "current_sprint": 2,
        "retry_count": 0,
        "sensors_passed": True,
    }
    assert route_after_evaluate(state) == "end"


def test_empty_contract_escalates():
    """An empty contract must not count as all-pass."""
    state = {"sprint_contract": [], "retry_count": 0}
    assert route_after_evaluate(state) == "escalate"


def test_sensor_failure_retries_even_if_criteria_pass():
    """pytest/ruff failure blocks a merge while retries remain."""
    state = {
        "sprint_contract": [{"id": "c1", "status": "PASS"}],
        "retry_count": 0,
        "sensors_passed": False,
    }
    assert route_after_evaluate(state) == "generate"


def test_agent_error_escalates_instead_of_merging():
    """A node error must not take the merge path."""
    state = {
        "sprint_contract": [{"id": "c1", "status": "PASS"}],
        "retry_count": 0,
        "error": "PlannerAgent failed: bad json",
    }
    assert route_after_evaluate(state) == "escalate"


def test_next_sprint_keeps_workspace_and_resets_retries():
    state = {
        "sprint_contract": [
            {"id": "c1", "sprint_id": 1, "status": "PASS", "evidence": "done"},
            {"id": "c2", "sprint_id": 2, "status": "FAIL", "evidence": "stale"},
        ],
        "current_sprint": 1,
        "retry_count": 2,
        "sprint_workspace": "/tmp/agentforge_keep",
        "eval_feedback": "old",
    }
    result = handle_next_sprint(state)
    assert result["current_sprint"] == 2
    assert result["retry_count"] == 0
    assert result["preserve_workspace"] is True
    assert result["sprint_workspace"] == "/tmp/agentforge_keep"
    assert result["eval_feedback"] is None
    assert result["sprint_contract"][1]["evidence"] == ""


def test_escalation_produces_error():
    """Escalation handler produces a structured error."""
    state = {
        "sprint_contract": [
            {"id": "c1", "description": "Create model", "status": "FAIL", "evidence": "not implemented"},
        ],
        "retry_count": 3,
    }
    result = handle_escalation(state)
    assert result["final_verdict"] == "FAIL"
    assert "Unresolved" in result["error"] or "unresolved" in result["error"].lower()
