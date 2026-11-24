"""Stable PGE failure codes. Messages are safe to store, stream, and log."""
from __future__ import annotations


class ModelCallError(RuntimeError):
    """A model call did not return a usable response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


MESSAGES = {
    "missing_model_config": (
        "DEEPSEEK_API_KEY is not set. The task was not sent to a model."
    ),
    "model_timeout": "The model request timed out. No partial result was accepted.",
    "model_failed": "The model request failed. The task did not succeed.",
    "execution_timeout": "The task exceeded TIMEOUT_SECONDS and was stopped. It did not succeed.",
    "sandbox_unavailable": (
        "The sandbox executor is not reachable. Generated code was not executed and was not run on the host."
    ),
    "sandbox_image_missing": (
        "The sandbox image is not present locally. "
        "Build it with `docker compose build sandbox` or `docker compose up --build`. "
        "Nothing was pulled and nothing ran on the host."
    ),
    "retrieval_unavailable": (
        "Retrieval storage is unavailable. Code search is off for this task. The task was not failed for that."
    ),
    "evaluation_failed": "Evaluation did not pass. This result is not PASS.",
    "task_failed": "The task failed before it could be accepted.",
}
