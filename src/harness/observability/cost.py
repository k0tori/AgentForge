from __future__ import annotations

from dataclasses import dataclass, field

# DeepSeek pricing (approximate, per 1M tokens)
DEEPSEEK_INPUT_PRICE_CNY = 1.0   # ¥1 per 1M input tokens
DEEPSEEK_OUTPUT_PRICE_CNY = 2.0  # ¥2 per 1M output tokens


@dataclass
class CostTracker:
    """Tracks token usage per agent role and estimates cost in CNY."""

    _usage: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, role: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Record token usage for an agent role."""
        if role not in self._usage:
            self._usage[role] = {"input_tokens": 0, "output_tokens": 0}
        self._usage[role]["input_tokens"] += input_tokens
        self._usage[role]["output_tokens"] += output_tokens

    def get_breakdown(self) -> dict:
        """Return cost breakdown matching section 6.3 format."""
        breakdown = {}
        total_tokens = 0
        total_cost = 0.0

        for role, usage in self._usage.items():
            input_tok = usage["input_tokens"]
            output_tok = usage["output_tokens"]
            tokens = input_tok + output_tok
            cost = (input_tok * DEEPSEEK_INPUT_PRICE_CNY + output_tok * DEEPSEEK_OUTPUT_PRICE_CNY) / 1_000_000

            breakdown[role] = {
                "tokens": tokens,
                "estimated_cost_cny": round(cost, 4),
            }
            total_tokens += tokens
            total_cost += cost

        breakdown["total"] = {
            "tokens": total_tokens,
            "estimated_cost_cny": round(total_cost, 4),
        }
        return breakdown

    def reset(self) -> None:
        """Reset all usage counters."""
        self._usage.clear()
