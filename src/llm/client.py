from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import settings
from src.llm.errors import MESSAGES, ModelCallError
from src.llm.redact import sanitize

logger = logging.getLogger(__name__)


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
    _max_retries: int = field(default=3, init=False)
    _base_delay: float = field(default=1.0, init=False)
    _max_delay: float = field(default=10.0, init=False)

    def __post_init__(self) -> None:
        self._model = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY or "missing",
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=settings.TEMPERATURE,
            max_tokens=4096,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )

    async def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute function with exponential backoff retry."""
        last_exception = None
        for attempt in range(self._max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self._max_retries - 1:
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        sanitize(str(e)),
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM call failed after %d attempts: %s",
                        self._max_retries,
                        sanitize(str(e)),
                    )
        raise last_exception

    async def chat(
        self,
        messages: list[BaseMessage],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AIMessage:
        """Send messages to the LLM and return the response with retry."""
        if not settings.DEEPSEEK_API_KEY.strip():
            raise ModelCallError("missing_model_config", MESSAGES["missing_model_config"])
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        try:
            response: AIMessage = await asyncio.wait_for(
                self._retry_with_backoff(self._model.ainvoke, messages, **kwargs),
                timeout=settings.LLM_TIMEOUT_SECONDS * self._max_retries,
            )
        except TimeoutError:
            raise ModelCallError("model_timeout", MESSAGES["model_timeout"]) from None
        except ModelCallError:
            raise
        except Exception:
            raise ModelCallError("model_failed", MESSAGES["model_failed"]) from None

        # Track token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            self._total_tokens += usage.get("total_tokens", 0)

        return response

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
