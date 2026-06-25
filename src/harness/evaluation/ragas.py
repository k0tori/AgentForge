from __future__ import annotations


class RAGASEvaluator:
    """RAGAS-style evaluation metrics (section 5.6).

    STUB: Full implementation deferred to Phase 2.
    Defines the interface for faithfulness, relevancy, and precision metrics.
    Current implementation returns 0.0 to indicate metrics are not yet implemented.
    """

    async def evaluate_faithfulness(self, answer: str, context: str) -> float:
        """Check if the answer is grounded in the given context.

        Returns: 0.0 (not faithful) to 1.0 (fully faithful)

        STUB: Returns 0.0 — not yet implemented.
        """
        raise NotImplementedError("RAGAS faithfulness evaluation not yet implemented")

    async def evaluate_answer_relevancy(self, question: str, answer: str) -> float:
        """Check if the answer is relevant to the question.

        Returns: 0.0 (not relevant) to 1.0 (fully relevant)

        STUB: Returns 0.0 — not yet implemented.
        """
        raise NotImplementedError("RAGAS answer relevancy evaluation not yet implemented")

    async def evaluate_context_precision(self, question: str, contexts: list[str]) -> float:
        """Check if retrieved contexts are relevant to the question.

        Returns: 0.0 (no precision) to 1.0 (perfect precision)

        STUB: Returns 0.0 — not yet implemented.
        """
        raise NotImplementedError("RAGAS context precision evaluation not yet implemented")

    async def evaluate_context_recall(self, answer: str, contexts: list[str]) -> float:
        """Check if the answer is supported by the retrieved contexts.

        Returns: 0.0 (no recall) to 1.0 (full recall)

        STUB: Returns 0.0 — not yet implemented.
        """
        raise NotImplementedError("RAGAS context recall evaluation not yet implemented")
