from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from src.config import settings


ActionHash = str


@dataclass
class LoopController:
    """Controls the PGE loop: iteration limits, timeouts, budgets, repeat detection."""

    max_iterations: int = settings.MAX_ITERATIONS
    max_sprint_retries: int = settings.MAX_SPRINT_RETRIES
    token_budget: int = settings.TOKEN_BUDGET
    timeout_seconds: int = settings.TIMEOUT_SECONDS
    seen_actions: set[ActionHash] = field(default_factory=set)
    repeat_threshold: int = 3
    _action_counts: dict[ActionHash, int] = field(default_factory=dict)
    _start_time: float = field(default_factory=time.time)
    _total_tokens: int = field(default=0)

    def check_iteration_budget(self, current: int) -> bool:
        """Return True if within budget (can continue)."""
        return current < self.max_iterations

    def check_token_budget(self, used: int) -> bool:
        """Return True if within token budget."""
        self._total_tokens = used
        return used < self.token_budget

    def check_timeout(self) -> bool:
        """Return True if within time budget."""
        elapsed = time.time() - self._start_time
        return elapsed < self.timeout_seconds

    def should_force_strategy_change(self, action_hash: ActionHash) -> bool:
        """Return True if this action has been repeated too many times."""
        count = self._action_counts.get(action_hash, 0)
        return count >= self.repeat_threshold

    def record_action(self, action_hash: ActionHash) -> None:
        """Record an action for repeat detection."""
        self._action_counts[action_hash] = self._action_counts.get(action_hash, 0) + 1
        self.seen_actions.add(action_hash)

    @staticmethod
    def hash_action(tool_name: str, args: dict) -> ActionHash:
        """Create a deterministic hash for a tool call."""
        content = f"{tool_name}:{sorted(args.items())}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def can_continue(self, iteration: int, tokens: int) -> bool:
        """Check all budgets at once. Return True if we can continue."""
        return (
            self.check_iteration_budget(iteration)
            and self.check_token_budget(tokens)
            and self.check_timeout()
        )
