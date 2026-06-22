from __future__ import annotations

import json
import re

from src.agents.base import BaseAgent
from src.agents.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT
from src.harness.context.disclosure import ProgressiveDisclosure
from src.workflow.state import AgentState, Criterion, Step


class PlannerAgent(BaseAgent):
    """Planner: analyzes codebase and produces plan + sprint contract."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_sprints = 5
        self.max_criteria = 10

    async def execute(self, state: AgentState) -> AgentState:
        """Analyze codebase and produce plan + sprint contract."""
        codebase_path = state.get("codebase_path", "./toy-repo")
        request = state.get("request", "")

        # Progressive disclosure: get codebase summary
        disclosure = ProgressiveDisclosure(codebase_path)
        codebase_summary = disclosure.summarize_codebase()

        # Read conventions
        conventions_path = f"{codebase_path}/CONVENTIONS.md"
        try:
            from src.tools.file_ops import read_file
            conventions = read_file(conventions_path)
        except FileNotFoundError:
            conventions = "No CONVENTIONS.md found."

        # Build prompt
        system = PLANNER_SYSTEM_PROMPT.format(
            codebase_summary=self._truncate(codebase_summary),
            conventions=self._truncate(conventions),
        )
        user = PLANNER_USER_PROMPT.format(request=request)

        messages = self._build_messages(system, user)

        # Call LLM with structured output
        from pydantic import BaseModel, Field

        class PlanOutput(BaseModel):
            plan: list[dict]
            sprint_contract: list[dict]

        result = await self.llm.chat_structured(messages, PlanOutput)

        # Parse and validate
        plan = [Step(**s).model_dump() for s in result.get("plan", [])][:self.max_sprints]
        contract = [Criterion(**c).model_dump() for c in result.get("sprint_contract", [])][:self.max_criteria]

        # Ensure all criteria start as FAIL
        for c in contract:
            c["status"] = "FAIL"
            c["evidence"] = ""

        return {
            **state,
            "plan": plan,
            "sprint_contract": contract,
            "current_sprint": 1,
        }
