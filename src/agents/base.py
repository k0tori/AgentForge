from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from langchain_core.messages import AIMessage, BaseMessage

from src.harness.sandbox.errors import SandboxUnavailableError
from src.llm.client import LLMClient
from src.llm.errors import MESSAGES, ModelCallError
from src.llm.redact import sanitize
from src.workflow.state import AgentState

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all PGE agents.

    Enforces the interface contract: each agent receives state and returns updated state.
    Agents cannot access each other's private state fields directly.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    @abstractmethod
    async def execute(self, state: AgentState) -> AgentState:
        """Execute the agent's role and return updated state."""
        ...

    async def safe_execute(self, state: AgentState) -> AgentState:
        """Execute with error handling. Wraps execute() with try-catch."""
        try:
            return await self.execute(state)
        except ModelCallError as exc:
            logger.error("Agent %s failed with %s", self.__class__.__name__, exc.code)
            return {**state, "error": str(exc), "error_code": exc.code}
        except SandboxUnavailableError as exc:
            logger.error("Agent %s failed with %s", self.__class__.__name__, exc.code)
            return {**state, "error": str(exc), "error_code": exc.code}
        except TimeoutError:
            logger.error("Agent %s timed out", self.__class__.__name__)
            return {**state, "error": MESSAGES["model_timeout"], "error_code": "model_timeout"}
        except Exception as exc:
            logger.error("Agent %s failed: %s", self.__class__.__name__, sanitize(str(exc)))
            return {**state, "error": MESSAGES["task_failed"], "error_code": "task_failed"}

    def _build_messages(self, system_prompt: str, user_content: str) -> list[BaseMessage]:
        """Build a message list from system prompt and user content."""
        return self.llm.build_messages(system_prompt, user_content)

    def _truncate(self, text: str, max_chars: int = 8000) -> str:
        """Truncate text to fit within context window."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... (truncated)"

    async def record_cost(
        self,
        messages: list[BaseMessage],
        state: AgentState,
        role: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AIMessage:
        """Call LLM and record token usage to the cost tracker in state."""
        response = await self.llm.chat(messages, tools=tools, tool_choice=tool_choice)
        tracker = state.get("cost_tracker")
        if tracker and hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            tracker.record(
                role=role,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
        return response
