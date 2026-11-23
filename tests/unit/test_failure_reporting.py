"""Fault injection for PGE failures. These tests do not call a paid model or start a container."""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from src.config import settings
from src.llm.client import LLMClient
from src.llm.errors import MESSAGES, ModelCallError
from src.llm.redact import sanitize
from src.workflow.failures import outcome_failed
from src.workflow.graph import run_task
from src.workflow.preflight import assess_retrieval, require_pge_ready


def test_sanitize_removes_key_bearer_and_url_password():
    raw = "bad sk-injected-secret-value Bearer abcdefghijklmn postgres://postgres:super-secret-pass@localhost/db"
    cleaned = sanitize(raw)
    assert "sk-injected" not in cleaned
    assert "abcdefghijklmn" not in cleaned
    assert "super-secret-pass" not in cleaned
    assert "[redacted]" in cleaned


def test_missing_key_and_missing_docker_are_distinct(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    with pytest.raises(Exception) as missing:
        require_pge_ready()
    assert missing.value.code == "missing_model_config"
    assert "sk-" not in str(missing.value)

    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
    monkeypatch.setattr("src.harness.sandbox.runtime.docker_ready", lambda: False)
    with pytest.raises(Exception) as docker:
        require_pge_ready()
    assert docker.value.code == "sandbox_unavailable"
    assert "sk-injected" not in str(docker.value)


def test_missing_image_does_not_ask_docker_to_pull(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
    monkeypatch.setattr("src.harness.sandbox.runtime.docker_ready", lambda: True)
    monkeypatch.setattr("src.harness.sandbox.runtime.image_present", lambda _image: False)
    with pytest.raises(Exception) as missing:
        require_pge_ready()
    assert missing.value.code == "sandbox_image_missing"
    assert "compose --profile sandbox build" in str(missing.value)
    assert "sk-injected" not in str(missing.value)


@pytest.mark.asyncio
async def test_retrieval_outage_is_a_degradation(monkeypatch):
    async def down() -> bool:
        return False

    monkeypatch.setattr("src.workflow.preflight.storage_available", down)
    notes = await assess_retrieval("toy-repo")
    assert notes[0]["code"] == "retrieval_unavailable"
    assert "://" not in notes[0]["message"]


@pytest.mark.asyncio
async def test_retrieval_degradation_still_allows_pass(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "SANDBOX_MODE", "host")

    async def down() -> bool:
        return False

    class _Graph:
        async def ainvoke(self, state):
            return {**state, "final_verdict": "PASS", "error": None}

    monkeypatch.setattr("src.workflow.preflight.storage_available", down)
    monkeypatch.setattr("src.workflow.graph.get_compiled_graph", lambda: _Graph())
    state = await run_task("say hello", "toy-repo", "retrieval-task")
    assert state["final_verdict"] == "PASS"
    assert not state.get("error")
    assert state["degradations"][0]["code"] == "retrieval_unavailable"
    assert "sk-injected" not in str(state["degradations"])


@pytest.mark.asyncio
async def test_model_timeout_and_failure_hide_the_key(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-injected-secret-value")
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 0.05)
    client = LLMClient()

    async def hang(_self, _func, *_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(LLMClient, "_retry_with_backoff", hang)
    with pytest.raises(ModelCallError) as timed:
        await client.chat([HumanMessage(content="hello")])
    assert timed.value.code == "model_timeout"
    assert "sk-injected" not in str(timed.value)

    async def explode(_self, _func, *_args, **_kwargs):
        raise RuntimeError(f"rejected {settings.DEEPSEEK_API_KEY}")

    monkeypatch.setattr(LLMClient, "_retry_with_backoff", explode)
    with pytest.raises(ModelCallError) as failed:
        await client.chat([HumanMessage(content="hello")])
    assert failed.value.code == "model_failed"
    assert str(failed.value) == MESSAGES["model_failed"]
    assert "sk-injected" not in str(failed.value)


def test_fail_verdict_is_not_success():
    assert outcome_failed({"final_verdict": "FAIL"}) is True
    assert outcome_failed({"final_verdict": "PASS"}) is False
    assert outcome_failed({"executor": "deterministic", "demo_completed": True, "final_verdict": None}) is False


def test_cli_missing_key_exits_nonzero(monkeypatch, capsys):
    from src.cli import main

    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    with pytest.raises(SystemExit) as exc:
        main(["add a helper", "--codebase", "toy-repo", "--task-id", "cli-missing"])
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "missing_model_config" in output
    assert "sk-" not in output
    assert "add a helper" not in output
