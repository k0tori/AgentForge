from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.messages import BaseMessage

from src.llm.client import LLMClient
from src.workflow.state import AgentState


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

    def _build_messages(self, system_prompt: str, user_content: str) -> list[BaseMessage]:
        """Build a message list from system prompt and user content."""
        return self.llm.build_messages(system_prompt, user_content)

    def _truncate(self, text: str, max_chars: int = 8000) -> str:
        """Truncate text to fit within context window."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... (truncated)"
