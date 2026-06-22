from __future__ import annotations


class RAGASEvaluator:
    """RAGAS-style evaluation metrics (section 5.6).

    STUB: Full implementation deferred to Phase 3.
    Defines the interface for faithfulness, relevancy, and precision metrics.
    """

    async def evaluate_faithfulness(self, answer: str, context: str) -> float:
        """Check if the answer is grounded in the given context.

        Returns: 0.0 (not faithful) to 1.0 (fully faithful)
        """
        # Stub: return 1.0
        return 1.0

    async def evaluate_answer_relevancy(self, question: str, answer: str) -> float:
        """Check if the answer is relevant to the question.

        Returns: 0.0 (not relevant) to 1.0 (fully relevant)
        """
        return 1.0

    async def evaluate_context_precision(self, question: str, contexts: list[str]) -> float:
        """Check if retrieved contexts are relevant to the question.

        Returns: 0.0 (no precision) to 1.0 (perfect precision)
        """
        return 1.0

    async def evaluate_context_recall(self, answer: str, contexts: list[str]) -> float:
        """Check if the answer is supported by the retrieved contexts.

        Returns: 0.0 (no recall) to 1.0 (full recall)
        """
        return 1.0
