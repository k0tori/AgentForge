from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

# Ensure all tool modules are imported so their registry.register() calls execute
# before any agent calls registry.to_langchain_tools().  The individual imports
# in agents/planner.py, agents/generator.py, agents/evaluator.py cover file_ops
# and test_ops, but code_ops (search_code) is not imported by the Generator's
# dependency chain.  Importing the modules here guarantees the full tool set is
# available when the LLM requests tool schemas.
import src.tools.code_ops  # noqa: F401
import src.tools.file_ops  # noqa: F401
import src.tools.test_ops  # noqa: F401
from src.agents.evaluator import EvaluatorAgent
from src.agents.generator import GeneratorAgent
from src.agents.planner import PlannerAgent
from src.harness.observability.cost import CostTracker  # noqa: I001
from src.harness.workspace import SprintWorkspace
from src.llm.client import LLMClient
from src.workflow.edges import handle_escalation, route_after_evaluate
from src.workflow.state import AgentState

logger = logging.getLogger(__name__)


def _route_and_cleanup(state: AgentState) -> str:
    """Route after evaluation AND handle sprint workspace lifecycle."""
    route = route_after_evaluate(state)
    sprint_workspace = state.get("sprint_workspace", "")

    if sprint_workspace:
        codebase_path = state.get("codebase_path", "")
        task_id = state.get("task_id", "default")
        sprint = state.get("current_sprint", 1)

        if route == "end":
            # All criteria PASS: merge workspace back to codebase
            workspace = SprintWorkspace(codebase_path, task_id, sprint)
            workspace.path = type(workspace.path)(sprint_workspace)
            workspace.merge()
            logger.info("Sprint %d PASSED — workspace merged", sprint)
        elif route == "escalate":
            # Max retries exceeded: discard workspace
            workspace = SprintWorkspace(codebase_path, task_id, sprint)
            workspace.path = type(workspace.path)(sprint_workspace)
            workspace.discard()
            logger.info("Sprint %d ESCALATED — workspace discarded", sprint)
        # route == "generate": keep workspace for next iteration

    return route


def build_graph() -> StateGraph:
    """Build the LangGraph StateGraph for the PGE loop.

    The graph IS the PGE loop (section 3.1):
    plan → generate → evaluate → conditional edge (end/generate/escalate)
    """
    llm = LLMClient()
    planner = PlannerAgent(llm)
    generator = GeneratorAgent(llm)
    evaluator = EvaluatorAgent(llm)

    graph = StateGraph(AgentState)

    # Add nodes with safe_execute for error handling
    graph.add_node("plan", planner.safe_execute)
    graph.add_node("generate", generator.safe_execute)
    graph.add_node("evaluate", evaluator.safe_execute)
    graph.add_node("escalate", handle_escalation)

    # Add edges
    graph.set_entry_point("plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "evaluate")

    # Conditional routing after evaluation (with workspace cleanup)
    graph.add_conditional_edges(
        "evaluate",
        _route_and_cleanup,
        {
            "end": END,
            "generate": "generate",
            "escalate": "escalate",
        },
    )

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
        "final_verdict": None,
        "error": None,
        "cost_tracker": cost_tracker,
    }

    graph = get_compiled_graph()
    final_state = await graph.ainvoke(initial_state)
    final_state["cost_breakdown"] = cost_tracker.get_breakdown()

    if final_state.get("error"):
        logger.error("Task %s failed: %s", task_id, final_state["error"])
    else:
        logger.info("Task %s completed with verdict: %s", task_id, final_state.get("final_verdict"))

    return final_state
