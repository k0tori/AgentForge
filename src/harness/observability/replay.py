from __future__ import annotations


class ReplayEngine:
    """Replay a previous execution from its trace (section 6.2).

    STUB: Full implementation deferred. Interface exists from day one.
    """

    def __init__(self, trace_data: dict) -> None:
        self.trace_data = trace_data

    async def replay(self) -> dict:
        """Re-execute the trace and return results.

        STUB: Returns the original trace data as-is.
        Full implementation will re-execute each step with the same inputs.
        """
        return self.trace_data

    def get_step(self, step_number: int) -> dict | None:
        """Get a specific step from the trace."""
        timeline = self.trace_data.get("timeline", [])
        for step in timeline:
            if step.get("step") == step_number:
                return step
        return None
