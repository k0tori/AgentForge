from __future__ import annotations


class ReplayEngine:
    """Replay a previous execution from its trace (section 6.2).

    STUB: Full implementation deferred to Phase 2.
    Interface exists from day one, but replay() raises NotImplementedError.
    """

    def __init__(self, trace_data: dict) -> None:
        self.trace_data = trace_data

    async def replay(self) -> dict:
        """Re-execute the trace and return results.

        STUB: Raises NotImplementedError.
        Full implementation will re-execute each step with the same inputs.
        """
        raise NotImplementedError("Replay engine not yet implemented")

    def get_step(self, step_number: int) -> dict | None:
        """Get a specific step from the trace."""
        timeline = self.trace_data.get("timeline", [])
        for step in timeline:
            if step.get("step") == step_number:
                return step
        return None
