from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationSample:
    """A known-quality code sample with human rating for calibration."""

    code_diff: str
    human_score: float  # 0-100
    description: str


class CalibrationSuite:
    """LLM-as-judge calibration (section 5.6).

    Stores known-quality samples, runs Evaluator against them,
    compares to human ratings, and reports calibration drift.
    """

    def __init__(self) -> None:
        self._samples: list[CalibrationSample] = []

    def add_sample(self, sample: CalibrationSample) -> None:
        """Add a calibration sample."""
        self._samples.append(sample)

    def get_samples(self) -> list[CalibrationSample]:
        """Get all calibration samples."""
        return self._samples

    async def run_calibration(self, evaluator_fn) -> dict:
        """Run evaluator against all calibration samples and compare to human ratings.

        Args:
            evaluator_fn: Async function that takes (code_diff, contract) and returns a score

        Returns:
            Calibration report with per-sample and aggregate statistics
        """
        results = []
        for sample in self._samples:
            # Use a dummy contract for calibration
            dummy_contract = [{"id": "calibration", "description": sample.description, "status": "FAIL"}]
            eval_result = await evaluator_fn(sample.code_diff, dummy_contract)
            score = eval_result.get("score", 0)
            drift = abs(score - sample.human_score)
            results.append({
                "description": sample.description,
                "human_score": sample.human_score,
                "evaluator_score": score,
                "drift": drift,
            })

        avg_drift = sum(r["drift"] for r in results) / len(results) if results else 0
        return {
            "samples": results,
            "average_drift": avg_drift,
            "needs_recalibration": avg_drift > 15,
        }
