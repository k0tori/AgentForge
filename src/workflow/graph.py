from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from langgraph.graph import END, StateGraph

# Ensure all tool modules are imported so their registry.register() calls execute
# before any agent calls registry.to_langchain_tools().  The individual imports
# in agents/planner.py, agents/generator.py, agents/evaluator.py cover file_ops
# and test_ops, but code_ops (search_code) is not imported by the Generator's
# dependency chain.  Importing the modules here guarantees the full tool set is
# available when the LLM requests tool schemas.
import src.tools.code_ops  # noqa: F401
import src.tools.file_ops  # noqa: F401
import src.tools.retrieval_ops  # noqa: F401
import src.tools.test_ops  # noqa: F401
from src.agents.evaluator import EvaluatorAgent
from src.agents.generator import GeneratorAgent
from src.agents.planner import PlannerAgent
from src.harness.observability.cost import CostTracker  # noqa: I001
from src.harness.observability.trace import TraceCollector, TraceSummary, observe_node
from src.harness.safety.rule_of_two import enforce_tool_set
from src.harness.workspace import SprintWorkspace
from src.llm.client import LLMClient
from src.retrieval.context import project_path_var
from src.retrieval.indexer import index_all
from src.tools.registry import registry
from src.workflow.edges import handle_escalation, handle_next_sprint, route_after_evaluate
from src.workflow.state import AgentState

logger = logging.getLogger(__name__)


def _discard_workspace(state: AgentState) -> None:
    """Drop a sprint workspace when the run stops without a passing merge."""
    sprint_workspace = state.get("sprint_workspace") or ""
    if not sprint_workspace:
        return
    workspace = SprintWorkspace(
        state.get("codebase_path", ""),
        state.get("task_id", "default"),
        state.get("current_sprint", 1),
    )
    workspace.path = type(workspace.path)(sprint_workspace)
    workspace.discard()


def _route_after_plan(state: AgentState) -> str:
    """Stop the graph when planning fails. There is no workspace to merge yet."""
    if state.get("error"):
        return "end"
    return "generate"


def _route_after_generate(state: AgentState) -> str:
    """Stop without merging when generation fails after a workspace was created."""
    if state.get("error"):
        _discard_workspace(state)
        return "end"
    return "evaluate"


def _route_and_cleanup(state: AgentState) -> str:
    """Route after evaluation AND handle sprint workspace lifecycle."""
    route = route_after_evaluate(state)
    sprint_workspace = state.get("sprint_workspace", "")

    if sprint_workspace:
        codebase_path = state.get("codebase_path", "")
        task_id = state.get("task_id", "default")
        sprint = state.get("current_sprint", 1)

        if route == "escalate":
            # Max retries exceeded: discard workspace
            workspace = SprintWorkspace(codebase_path, task_id, sprint)
            workspace.path = type(workspace.path)(sprint_workspace)
            workspace.discard()
            logger.info("Sprint %d ESCALATED — workspace discarded", sprint)
        # route == "generate": keep workspace for next iteration

    return route


def publish_branch(state: AgentState) -> AgentState:
    """Commit a passing workspace onto its own branch and drop the temp dir."""
    sprint_workspace = state.get("sprint_workspace") or ""
    if not sprint_workspace:
        return {**state, "result_branch": "", "error": "No sprint workspace to publish", "final_verdict": "FAIL"}
    workspace = SprintWorkspace(
        state.get("codebase_path", ""),
        state.get("task_id", "default"),
        int(state.get("current_sprint") or 1),
    )
    workspace.path = Path(sprint_workspace)
    try:
        branch = workspace.publish()
    except Exception as exc:
        logger.exception("Failed to publish sprint workspace")
        return {**state, "error": f"Failed to publish branch: {exc}", "final_verdict": "FAIL"}
    finally:
        workspace.discard()
    logger.info("Published passing run to %s", branch)
    return {**state, "result_branch": branch, "error": None}


def build_graph() -> StateGraph:
    """Build the LangGraph StateGraph for the PGE loop.

    The graph IS the PGE loop (section 3.1):
    plan → generate → evaluate → conditional edge (end/generate/escalate)
    """
    enforce_tool_set(registry.get_tool_names())

    llm = LLMClient()
    planner = PlannerAgent(llm)
    generator = GeneratorAgent(llm)
    evaluator = EvaluatorAgent(llm)

    graph = StateGraph(AgentState)

    # Add nodes with safe_execute for error handling
    graph.add_node("plan", observe_node("planner", planner.safe_execute))
    graph.add_node("generate", observe_node("generator", generator.safe_execute))
    graph.add_node("evaluate", observe_node("evaluator", evaluator.safe_execute))
    graph.add_node("escalate", observe_node("escalate", handle_escalation))
    graph.add_node("next_sprint", observe_node("next_sprint", handle_next_sprint))
    graph.add_node("publish", observe_node("publish", publish_branch))

    # Add edges
    graph.set_entry_point("plan")
    graph.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"generate": "generate", "end": END},
    )
    graph.add_conditional_edges(
        "generate",
        _route_after_generate,
        {"evaluate": "evaluate", "end": END},
    )

    # Conditional routing after evaluation (with workspace cleanup)
    graph.add_conditional_edges(
        "evaluate",
        _route_and_cleanup,
        {
            "end": "publish",
            "next_sprint": "next_sprint",
            "generate": "generate",
            "escalate": "escalate",
        },
    )

    graph.add_edge("next_sprint", "generate")
    graph.add_edge("publish", END)
    graph.add_edge("escalate", END)

    return graph


# Compiled graph singleton
_compiled_graph = None


def get_compiled_graph():
    """Get or compile the StateGraph."""
    global _compiled_graph
    if _compiled_graph is None:
        graph = build_graph()
        _compiled_graph = graph.compile()
    return _compiled_graph


async def run_task(request: str, codebase_path: str, task_id: str = "") -> AgentState:
    """Run a complete PGE task.

    This is the main entry point for task execution.
    """
    import uuid

    if not task_id:
        task_id = str(uuid.uuid4())

    logger.info("Starting task %s", task_id)

    cost_tracker = CostTracker()
    collector = TraceCollector()
    collector.start_trace(task_id)
    started = time.perf_counter()
    project_token = project_path_var.set(os.path.abspath(codebase_path))

    initial_state: AgentState = {
        "request": request,
        "plan": [],
        "sprint_contract": [],
        "execution_trace": [],
        "code_diff": "",
        "eval_feedback": None,
        "retry_count": 0,
        "task_id": task_id,
        "codebase_path": codebase_path,
        "sprint_workspace": "",
        "current_sprint": 1,
        "preserve_workspace": False,
        "result_branch": "",
        "final_verdict": None,
        "sensors_passed": True,
        "error": None,
        "cost_tracker": cost_tracker,
        "trace_collector": collector,
    }

    try:
        await index_all(codebase_path)
        graph = get_compiled_graph()
        final_state = await graph.ainvoke(initial_state)
    finally:
        project_path_var.reset(project_token)

    breakdown = cost_tracker.get_breakdown()
    final_state["cost_breakdown"] = breakdown
    verdict = final_state.get("final_verdict") or ""
    collector.finalize(TraceSummary(
        total_steps=len(collector.timeline),
        total_duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        total_tokens=breakdown.get("total", {}).get("tokens", 0),
        sprints_completed=1 if verdict.startswith("PASS") and not final_state.get("error") else 0,
        sprint_retries=final_state.get("retry_count") or 0,
        final_verdict=verdict or ("FAIL" if final_state.get("error") else ""),
    ))
    final_state["trace"] = collector.to_dict()
    final_state.pop("trace_collector", None)

    if final_state.get("error"):
        logger.error("Task %s failed: %s", task_id, final_state["error"])
    else:
        logger.info("Task %s completed with verdict: %s", task_id, final_state.get("final_verdict"))

    return final_state
