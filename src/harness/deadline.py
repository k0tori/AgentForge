"""Wall-clock deadline for one PGE run.

An asyncio timeout cancels awaits. It does not stop a thread, and it does not
interrupt a git command that has already entered the kernel. Callers check this
deadline before starting another side effect, and after a blocking call returns.
"""
from __future__ import annotations

import time
from contextvars import ContextVar, Token

_deadline: ContextVar[float | None] = ContextVar("agentforge_deadline", default=None)


class DeadlineExceededError(TimeoutError):
    """The PGE deadline passed. Further publish and success writes must not proceed."""

    def __init__(self, message: str = "PGE deadline exceeded", *, branch: str | None = None) -> None:
        super().__init__(message)
        self.branch = branch


def bind_deadline(seconds: float) -> Token:
    return _deadline.set(time.monotonic() + seconds)


def reset_deadline(token: Token) -> None:
    _deadline.reset(token)


def deadline_exceeded() -> bool:
    limit = _deadline.get()
    return limit is not None and time.monotonic() >= limit
