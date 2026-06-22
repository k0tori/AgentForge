from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import settings


@dataclass
class TokenUsage:
    """Token usage tracking per call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMClient:
    """Unified LLM client wrapping DeepSeek via OpenAI-compatible API."""

    _model: ChatOpenAI = field(init=False)
    _total_tokens: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._model = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=0.1,
            max_tokens=4096,
        )

    async def chat(
        self,
        messages: list[BaseMessage],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AIMessage:
        """Send messages to the LLM and return the response."""
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        response: AIMessage = await self._model.ainvoke(messages, **kwargs)

        # Track token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            self._total_tokens += usage.get("total_tokens", 0)

        return response

    async def chat_structured(self, messages: list[BaseMessage], schema: type) -> dict:
        """Send messages and get a structured response matching the given schema."""
        structured_model = self._model.with_structured_output(schema)
        result = await structured_model.ainvoke(messages)
        return result if isinstance(result, dict) else result.model_dump()

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        history: list[BaseMessage] | None = None,
    ) -> list[BaseMessage]:
        """Build a message list from system prompt, optional history, and user content."""
        msgs: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        if history:
            msgs.extend(history)
        msgs.append(HumanMessage(content=user_content))
        return msgs
