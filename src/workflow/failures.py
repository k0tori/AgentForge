"""Classify a PGE failure without copying prompts or the process environment."""
from __future__ import annotations

from typing import Any

from src.harness.sandbox.errors import SandboxUnavailableError
from src.llm.errors import MESSAGES, ModelCallError
from src.llm.redact import sanitize


class TaskFailureError(RuntimeError):
    """A required PGE condition failed before the run could be accepted."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or MESSAGES.get(code, MESSAGES["task_failed"]))


def classify_text(text: str) -> tuple[str, str]:
    """Map an already-caught message to a stable code and a safe sentence."""
    cleaned = sanitize(text)
    lowered = cleaned.lower()
    if "sandbox image" in lowered or "not present locally" in lowered or "unable to find image" in lowered:
        return "sandbox_image_missing", MESSAGES["sandbox_image_missing"]
    if "docker" in lowered or "sandbox" in lowered:
        return "sandbox_unavailable", MESSAGES["sandbox_unavailable"]
    if "timed out" in lowered or "timeout" in lowered:
        return "model_timeout", MESSAGES["model_timeout"]
    if "api key" in lowered or "not sent to a model" in lowered:
        return "missing_model_config", MESSAGES["missing_model_config"]
    if "evaluation did not pass" in lowered:
        return "evaluation_failed", MESSAGES["evaluation_failed"]
    return "task_failed", MESSAGES["task_failed"]


def classify_exception(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, TaskFailureError):
        return exc.code, str(exc)
    if isinstance(exc, ModelCallError):
        return exc.code, str(exc)
    if isinstance(exc, SandboxUnavailableError):
        code = getattr(exc, "code", "sandbox_unavailable")
        return code, MESSAGES.get(code, MESSAGES["sandbox_unavailable"])
    if isinstance(exc, TimeoutError):
        return "execution_timeout", MESSAGES["execution_timeout"]
    return classify_text(str(exc))


def outcome_failed(state: dict[str, Any]) -> bool:
    """True when a PGE result must not be reported as success."""
    if state.get("executor") == "deterministic" and state.get("demo_completed") and not state.get("error"):
        return False
    if state.get("error") or state.get("error_code"):
        return True
    return state.get("final_verdict") not in ("PASS", "PASS_WITH_WARNINGS")


def failed_state(
    *,
    task_id: str,
    codebase_path: str,
    code: str,
    message: str | None = None,
    degradations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Terminal PGE state. It has no trace and no request body."""
    safe = message or MESSAGES.get(code, MESSAGES["task_failed"])
    return {
        "task_id": task_id,
        "codebase_path": codebase_path,
        "error": safe,
        "error_code": code,
        "final_verdict": "FAIL",
        "acceptance": "failed",
        "executor": "pge",
        "demo_completed": False,
        "degradations": degradations or [],
        "result_branch": "",
        "code_diff": "",
        "trace": None,
        "note": safe,
    }
