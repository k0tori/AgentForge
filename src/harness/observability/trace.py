from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ToolCallRecord:
    """A single tool call within a trace step."""

    tool: str
    args: dict
    result: str = ""


@dataclass
class TraceStep:
    """A single step in the execution trace."""

    step: int
    agent: str
    action: str
    thought: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    duration_ms: int = 0
    tokens_used: int = 0
    sprint: int | None = None


@dataclass
class TraceSummary:
    """Summary statistics for a complete trace."""

    total_steps: int = 0
    total_duration_ms: int = 0
    total_tokens: int = 0
    sprints_completed: int = 0
    sprint_retries: int = 0
    final_verdict: str = ""


@dataclass
class TraceCollector:
    """Collects execution trace data for observability (section 6.1)."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    timeline: list[TraceStep] = field(default_factory=list)
    summary: TraceSummary = field(default_factory=TraceSummary)
    _start_time: float = 0

    def start_trace(self, task_id: str) -> None:
        """Begin a new trace."""
        self.task_id = task_id
        self.timeline = []
        self._start_time = datetime.now(UTC).timestamp()

    def add_step(
        self,
        agent: str,
        action: str,
        thought: str = "",
        tool_calls: list[ToolCallRecord] | None = None,
        duration_ms: int = 0,
        tokens_used: int = 0,
        sprint: int | None = None,
    ) -> None:
        """Add a step to the trace."""
        step = TraceStep(
            step=len(self.timeline) + 1,
            agent=agent,
            action=action,
            thought=thought,
            tool_calls=tool_calls or [],
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            sprint=sprint,
        )
        self.timeline.append(step)

    def finalize(self, summary: TraceSummary | None = None) -> None:
        """Finalize the trace with summary statistics."""
        if summary:
            self.summary = summary
        else:
            self.summary = TraceSummary(
                total_steps=len(self.timeline),
                total_duration_ms=sum(s.duration_ms for s in self.timeline),
                total_tokens=sum(s.tokens_used for s in self.timeline),
            )

    def to_dict(self) -> dict:
        """Export trace as a dictionary matching section 6.1 format."""
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "timeline": [
                {
                    "step": s.step,
                    "agent": s.agent,
                    "action": s.action,
                    "thought": s.thought,
                    "tool_calls": [
                        {"tool": tc.tool, "args": tc.args, "result": tc.result}
                        for tc in s.tool_calls
                    ],
                    "duration_ms": s.duration_ms,
                    "tokens_used": s.tokens_used,
                    **({"sprint": s.sprint} if s.sprint is not None else {}),
                }
                for s in self.timeline
            ],
            "summary": {
                "total_steps": self.summary.total_steps,
                "total_duration_ms": self.summary.total_duration_ms,
                "total_tokens": self.summary.total_tokens,
                "sprints_completed": self.summary.sprints_completed,
                "sprint_retries": self.summary.sprint_retries,
                "final_verdict": self.summary.final_verdict,
            },
        }


def record_node(collector: TraceCollector, agent: str, result: dict, started: float) -> None:
    """Append one graph node to the trace, including generator tool calls."""
    tool_calls: list[ToolCallRecord] = []
    if agent == "generator":
        for item in result.get("execution_trace") or []:
            if not isinstance(item, dict):
                continue
            args = item.get("args")
            tool_calls.append(ToolCallRecord(
                tool=str(item.get("tool_name", "")),
                args=args if isinstance(args, dict) else {},
                result=str(item.get("result", ""))[:500],
            ))
    error = result.get("error")
    collector.add_step(
        agent=agent,
        action="error" if error else agent,
        thought=str(error or result.get("final_verdict") or ""),
        tool_calls=tool_calls,
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        sprint=result.get("current_sprint"),
    )


def observe_node(agent_name: str, fn):
    """Wrap a graph node so its result is recorded on the state's TraceCollector."""

    async def node(state: dict) -> dict:
        started = time.perf_counter()
        result = fn(state)
        if inspect.isawaitable(result):
            result = await result
        collector = None
        if isinstance(result, dict):
            collector = result.get("trace_collector") or state.get("trace_collector")
        if isinstance(collector, TraceCollector) and isinstance(result, dict):
            record_node(collector, agent_name, result, started)
        return result

    return node
