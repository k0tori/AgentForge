"""Command line entry point: agentforge "do the thing" --codebase ./toy-repo"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from src.config import settings
from src.workflow.graph import run_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentforge")
    parser.add_argument("request", help="What the agent should change")
    parser.add_argument("--codebase", default=settings.TOY_REPO_PATH, help="Repository to edit")
    parser.add_argument("--task-id", default="", help="Optional task id")
    return parser


def main(argv: list[str] | None = None) -> None:
    from src.llm.redact import sanitize
    from src.workflow.failures import classify_exception, outcome_failed

    args = build_parser().parse_args(argv)
    try:
        state = asyncio.run(run_task(args.request, args.codebase, args.task_id))
    except Exception as exc:
        code, message = classify_exception(exc)
        state = {"task_id": args.task_id, "error_code": code, "error": message, "final_verdict": "FAIL"}
    failed = outcome_failed(state)
    summary = {
        "task_id": state.get("task_id"),
        "status": "failed" if failed else "completed",
        "final_verdict": state.get("final_verdict"),
        "error_code": state.get("error_code"),
        "error": sanitize(str(state.get("error"))) if state.get("error") else None,
        "degradations": state.get("degradations") or [],
        "result_branch": state.get("result_branch"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(1 if failed else 0)
