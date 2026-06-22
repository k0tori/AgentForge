from __future__ import annotations

from src.workflow.state import AgentState, Criterion, Step


def test_criterion_default_is_fail():
    """Every new criterion must start as FAIL."""
    c = Criterion(id="c1", description="test criterion")
    assert c.status == "FAIL"
    assert c.evidence == ""


def test_step_creation():
    """Test Step model creation."""
    s = Step(id=1, description="Create Tag model", dependencies=[])
    assert s.id == 1
    assert s.dependencies == []


def test_step_with_dependencies():
    """Test Step with dependencies."""
    s = Step(id=2, description="Create CRUD", dependencies=[1])
    assert s.dependencies == [1]


def test_criterion_can_be_pass():
    """Test that criterion status can be set to PASS."""
    c = Criterion(id="c1", description="test", status="PASS", evidence="tests passed")
    assert c.status == "PASS"
    assert c.evidence == "tests passed"
