"""Executor request rules. These tests do not start Docker."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.harness.sandbox import runtime as sandbox_runtime
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.protocol import NAME_RE, ProtocolError, decode_request, encode
from src.harness.sandbox.workspace_guard import assert_workspace_root, open_attempt


def _run(name: str = "ab" * 16, **extra: object) -> dict:
    payload: dict[str, object] = {"op": "run", "name": name, "cmd": "pytest", "timeout": 5}
    payload.update(extra)
    return payload


def test_run_frame_rejects_image_mounts_shell_and_cleanup():
    decode_request(json.dumps({"op": "ping"}).encode())
    decode_request(json.dumps({"op": "prepare"}).encode())
    decode_request(json.dumps(_run()).encode())
    with pytest.raises(ProtocolError) as image:
        decode_request(json.dumps(_run(image="alpine")).encode())
    assert image.value.reason == "bad_field"
    with pytest.raises(ProtocolError) as shell:
        decode_request(json.dumps(_run(cmd="bash")).encode())
    assert shell.value.reason == "bad_command"
    with pytest.raises(ProtocolError) as cleanup:
        decode_request(json.dumps({"op": "cleanup", "name": "ab" * 16}).encode())
    assert cleanup.value.reason == "bad_op"
    with pytest.raises(ProtocolError):
        decode_request(json.dumps(_run(target="../secret")).encode())
    with pytest.raises(ProtocolError):
        decode_request(json.dumps(_run(target="/etc/passwd")).encode())
    with pytest.raises(ProtocolError):
        decode_request(json.dumps(_run(cmd="ruff", target="x.py")).encode())
    with pytest.raises(ProtocolError):
        decode_request(b"{" + b"x" * 5000)
    encoded = encode({"ok": False, "error": "unauthorized"})
    assert encoded.startswith(len(encoded[4:]).to_bytes(4, "big"))
    assert NAME_RE.fullmatch("ab" * 16)


def test_workspace_root_rejects_a_writable_parent_and_a_symlink(tmp_path: Path):
    root = tmp_path / "var" / "lib" / "agentforge" / "workspaces"
    root.mkdir(parents=True)
    root.chmod(0o755)
    checked = assert_workspace_root(root, 10002, 10002)
    name = "ab" * 16
    (checked / name).mkdir(mode=0o700)
    descriptor, opened = open_attempt(checked, name, 10002, 10002)
    os.close(descriptor)
    assert opened == checked / name
    link_name = "cd" * 16
    (checked / link_name).symlink_to(name)
    with pytest.raises(SandboxUnavailableError):
        open_attempt(checked, link_name, 10002, 10002)
    with pytest.raises(SandboxUnavailableError):
        open_attempt(checked, "..", 10002, 10002)
    root.chmod(0o777)
    with pytest.raises(SandboxUnavailableError):
        assert_workspace_root(root, 10002, 10002)


def test_api_that_can_open_docker_is_not_ready(monkeypatch):
    monkeypatch.setattr("src.harness.sandbox.runtime.api_can_reach_docker_socket", lambda: True)

    def explode():
        raise AssertionError("executor was contacted after the socket was reachable")

    monkeypatch.setattr("src.harness.sandbox.client.ping", explode)
    assert sandbox_runtime.docker_ready() is False
    assert "not hardened" in sandbox_runtime.last_sandbox_block_reason
