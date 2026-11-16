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
    args = build_parser().parse_args(argv)
    state = asyncio.run(run_task(args.request, args.codebase, args.task_id))
    summary = {
        "task_id": state.get("task_id"),
        "final_verdict": state.get("final_verdict"),
        "error": state.get("error"),
        "trace": state.get("trace"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    raise SystemExit(1 if state.get("error") else 0)
