from __future__ import annotations

import hashlib
from collections import Counter


class RepeatDetector:
    """Detects when the same tool call is repeated too many times."""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._counts: Counter[str] = Counter()

    def record(self, tool_name: str, args: dict) -> None:
        """Record a tool call."""
        h = self._hash(tool_name, args)
        self._counts[h] += 1

    def is_repeated(self, tool_name: str, args: dict) -> bool:
        """Check if this exact tool call has been repeated >= threshold times."""
        h = self._hash(tool_name, args)
        return self._counts[h] >= self.threshold

    def get_count(self, tool_name: str, args: dict) -> int:
        """Get how many times this exact tool call has been made."""
        h = self._hash(tool_name, args)
        return self._counts[h]

    def reset(self) -> None:
        """Reset all counters."""
        self._counts.clear()

    @staticmethod
    def _hash(tool_name: str, args: dict) -> str:
        content = f"{tool_name}:{sorted(args.items())}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
