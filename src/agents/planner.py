from __future__ import annotations

import json
import re

from src.agents.base import BaseAgent
from src.agents.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT
from src.harness.context.disclosure import ProgressiveDisclosure
from src.tools.file_ops import read_file
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

        # Call LLM (regular chat, parse JSON manually for DeepSeek compatibility)
        response = await self.llm.chat(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)

        # Extract JSON from response
        result = self._extract_json(content)

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

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON object from LLM response text."""
        # Try to find JSON block in markdown code fence
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        # Try to find raw JSON object using bracket counting
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in response: {text[:200]}")

        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])

        raise ValueError(f"Could not extract JSON from response: {text[:200]}")
