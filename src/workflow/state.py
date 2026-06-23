from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class Step(BaseModel):
    """A single step in the execution plan."""

    id: int
    description: str
    dependencies: list[int] = Field(default_factory=list)


class Criterion(BaseModel):
    """A single acceptance criterion in the sprint contract.

    Default status is FAIL — Generator must provide evidence to flip to PASS.
    """

    id: str
    description: str
    status: Literal["FAIL", "PASS"] = "FAIL"
    evidence: str = ""


class ToolCall(BaseModel):
    """A recorded tool call in the execution trace."""

    tool_name: str
    args: dict
    result: str = ""
    timestamp: float = 0.0


class AgentState(TypedDict, total=False):
    """State that flows through the LangGraph StateGraph (section 4.1).

    Every node reads from and writes to this state.
    """

    request: str  # User's functional request
    plan: list[dict]  # Planner output: list of Steps
    sprint_contract: list[dict]  # Acceptance criteria, all start FAIL
    execution_trace: list[dict]  # Generator's tool calls (only Generator sees this)
    code_diff: str  # Generator's final code output
    eval_feedback: str | None  # Evaluator's specific feedback for retries
    retry_count: int  # Current retry count
    task_id: str  # Task UUID for tracking
    codebase_path: str  # Path to the target codebase
    current_sprint: int  # Current sprint number
    final_verdict: str | None  # Sprint-level verdict (report only, not routing)
    error: str | None  # Error message if task failed
