from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.evaluator import EvaluatorAgent
from src.agents.generator import GeneratorAgent
from src.agents.planner import PlannerAgent
from src.llm.client import LLMClient
from src.workflow.edges import handle_escalation, route_after_evaluate
from src.workflow.state import AgentState


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

    # Add nodes
    graph.add_node("plan", planner.execute)
    graph.add_node("generate", generator.execute)
    graph.add_node("evaluate", evaluator.execute)
    graph.add_node("escalate", handle_escalation)

    # Add edges
    graph.set_entry_point("plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "evaluate")

    # Conditional routing after evaluation
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
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
        "current_sprint": 1,
        "final_verdict": None,
        "error": None,
    }

    graph = get_compiled_graph()
    final_state = await graph.ainvoke(initial_state)
    return final_state
