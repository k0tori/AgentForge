"""Wiring tests for trace, tool safety, retrieval cache, and the CLI."""
from __future__ import annotations

import json
import time

import pytest

from src.cli import build_parser
from src.harness.evaluation.fresh_context import EvalInput, FreshContextEvaluator
from src.harness.observability.trace import TraceCollector, record_node
from src.harness.safety.rule_of_two import validate_tool_set
from src.retrieval.indexer import index_all
from src.retrieval.search import search
from src.tools.registry import registry


def test_registered_tools_satisfy_rule_of_two():
    import src.workflow.graph  # noqa: F401  # registers every tool

    assert validate_tool_set(registry.get_tool_names()) == []


def test_record_node_keeps_generator_tool_calls():
    collector = TraceCollector()
    collector.start_trace("task-1")
    record_node(
        collector,
        "generator",
        {
            "current_sprint": 1,
            "execution_trace": [
                {"tool_name": "write_file", "args": {"path": "a.py"}, "result": "ok"},
            ],
        },
        time.perf_counter(),
    )
    exported = collector.to_dict()
    assert exported["timeline"][0]["agent"] == "generator"
    assert exported["timeline"][0]["tool_calls"][0]["tool"] == "write_file"


def test_eval_input_rejects_embedded_trace():
    trace = [{"tool_name": "read_file", "args": {"path": "secret.py"}, "result": "token"}]
    leaked = EvalInput(code_diff=json.dumps(trace), sprint_contract=[])
    assert FreshContextEvaluator.validate_no_leakage(leaked, {"execution_trace": trace}) is False
    clean = EvalInput(code_diff="diff --git a/a.py", sprint_contract=[])
    assert FreshContextEvaluator.validate_no_leakage(clean, {"execution_trace": trace}) is True


def test_cli_parses_request_and_codebase():
    args = build_parser().parse_args(["add a tag resource", "--codebase", "toy-repo"])
    assert args.request == "add a tag resource"
    assert args.codebase == "toy-repo"


@pytest.mark.asyncio
async def test_search_uses_cache_without_embedding(monkeypatch):
    async def cached(_key: str):
        return [{"id": "1", "content": "cached chunk", "similarity": 0.9}]

    def explode(_text: str):
        raise AssertionError("cache hit must not embed")

    monkeypatch.setattr("src.retrieval.search.get_retrieval_cache", cached)
    monkeypatch.setattr("src.retrieval.search.embedding_generator.generate_embedding", explode)
    results = await search("where is the user model", top_k=3, project_path="/tmp/repo")
    assert results[0]["content"] == "cached chunk"


@pytest.mark.asyncio
async def test_index_all_skips_when_database_is_down(monkeypatch):
    async def unavailable() -> bool:
        return False

    monkeypatch.setattr("src.retrieval.indexer.storage_available", unavailable)
    assert await index_all("/tmp/does-not-matter") == {"code_chunks": 0, "conventions": 0}
