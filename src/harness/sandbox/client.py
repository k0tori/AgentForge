"""API-side client for the sandbox executor. This module does not call Docker."""
from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass

from src.config import settings
from src.harness.deadline import DeadlineExceededError, deadline_exceeded
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.protocol import (
    MAX_RESPONSE_BYTES,
    ProtocolError,
    decode_request,
    read_frame,
    send_frame,
)

_UNAVAILABLE = "Sandbox executor is not reachable. Nothing ran on the host."


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False


def ping(timeout: float = 5) -> dict:
    return _transact({"op": "ping"}, timeout)


def prepare(timeout: float = 10) -> str:
    if deadline_exceeded():
        raise DeadlineExceededError("PGE deadline exceeded before creating a sandbox workspace")
    payload = _transact({"op": "prepare"}, timeout)
    name = payload.get("name")
    if not payload.get("ok") or not isinstance(name, str):
        raise SandboxUnavailableError("Sandbox executor did not create a workspace. Nothing ran on the host.")
    return name


def run(name: str, cmd: str, timeout: int, target: str = "") -> RunResult:
    if deadline_exceeded():
        raise DeadlineExceededError("PGE deadline exceeded before starting a sandbox command")
    payload: dict[str, object] = {"op": "run", "name": name, "cmd": cmd, "timeout": timeout}
    if target:
        payload["target"] = target
    try:
        decode_request(json.dumps(payload).encode("utf-8"))
    except ProtocolError as exc:
        raise SandboxUnavailableError("Sandbox command was rejected before it was sent.") from exc
    body = _transact(payload, timeout + 5)
    if body.get("error") == "timeout":
        raise subprocess.TimeoutExpired(cmd, timeout)
    if not body.get("ok"):
        raise SandboxUnavailableError(
            "Sandbox executor rejected the command. Nothing ran on the host."
        )
    return RunResult(
        returncode=int(body.get("exit_code", 1)),
        stdout=str(body.get("stdout", "")),
        stderr=str(body.get("stderr", "")),
        truncated=bool(body.get("truncated", False)),
    )


def _transact(payload: dict, timeout: float) -> dict:
    if deadline_exceeded():
        raise DeadlineExceededError("PGE deadline exceeded before contacting the sandbox executor")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(settings.SANDBOX_EXECUTOR_SOCK)
        send_frame(sock, payload)
        raw = read_frame(sock, MAX_RESPONSE_BYTES)
    except (ProtocolError, OSError) as exc:
        if deadline_exceeded():
            raise DeadlineExceededError("PGE deadline exceeded while a sandbox command was running") from exc
        raise SandboxUnavailableError(_UNAVAILABLE) from exc
    finally:
        sock.close()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SandboxUnavailableError(_UNAVAILABLE) from exc
    if not isinstance(body, dict):
        raise SandboxUnavailableError(_UNAVAILABLE)
    return body
