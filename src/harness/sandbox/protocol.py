"""Fixed frames for the sandbox executor socket.

A request names a pytest or ruff run. It cannot name an image, a mount, an
environment list, a Docker flag, or a shell command.
"""
from __future__ import annotations

import json
import re
import socket

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 48_000
MAX_OUTPUT_BYTES = 16_384
NAME_RE = re.compile(r"^[a-f0-9]{32}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9_./+-]{1,180}$")
PYTEST_TIMEOUT_MAX = 120
RUFF_TIMEOUT_MAX = 30


class ProtocolError(ValueError):
    """The frame is not a request this executor accepts."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def encode(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProtocolError("response_too_large")
    return len(body).to_bytes(4, "big") + body


def decode_request(body: bytes) -> dict:
    if len(body) > MAX_REQUEST_BYTES:
        raise ProtocolError("request_too_large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("bad_request") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("bad_request")
    op = payload.get("op")
    if op == "ping":
        allowed = {"op"}
    elif op == "prepare":
        allowed = {"op"}
    elif op == "run":
        allowed = {"op", "name", "cmd", "timeout", "target"}
    else:
        raise ProtocolError("bad_op")
    if set(payload) - allowed:
        raise ProtocolError("bad_field")
    if op == "run":
        _validate_run(payload)
    return payload


def _validate_run(payload: dict) -> None:
    name = payload.get("name")
    cmd = payload.get("cmd")
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        raise ProtocolError("bad_name")
    if cmd not in {"pytest", "ruff"}:
        raise ProtocolError("bad_command")
    timeout = payload.get("timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ProtocolError("bad_timeout")
    limit = PYTEST_TIMEOUT_MAX if cmd == "pytest" else RUFF_TIMEOUT_MAX
    if timeout < 1 or timeout > limit:
        raise ProtocolError("bad_timeout")
    if cmd == "ruff":
        if "target" in payload:
            raise ProtocolError("bad_field")
        return
    if "target" not in payload:
        return
    target = payload.get("target")
    if not isinstance(target, str) or not safe_target(target):
        raise ProtocolError("bad_target")


def safe_target(target: str) -> bool:
    if target.startswith(("-", "/")) or ".." in target.split("/"):
        return False
    return TARGET_RE.fullmatch(target) is not None


def argv_for(cmd: str, target: str = "") -> list[str]:
    """Build the only argument vectors the sandbox image is allowed to execute."""
    if cmd == "pytest":
        argv = ["python", "-m", "pytest", "-v", "--tb=short"]
        if target:
            if not safe_target(target):
                raise ProtocolError("bad_target")
            argv.append(target)
        return argv
    if cmd == "ruff":
        if target:
            raise ProtocolError("bad_field")
        return ["python", "-m", "ruff", "check", "--output-format=concise", "."]
    raise ProtocolError("bad_command")


def read_frame(conn: socket.socket, limit: int) -> bytes:
    header = _read_exact(conn, 4)
    if header is None:
        raise ProtocolError("disconnected")
    size = int.from_bytes(header, "big")
    if size < 2 or size > limit:
        raise ProtocolError("request_too_large")
    body = _read_exact(conn, size)
    if body is None:
        raise ProtocolError("disconnected")
    return body


def _read_exact(conn: socket.socket, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            part = conn.recv(remaining)
        except TimeoutError as exc:
            raise ProtocolError("timeout") from exc
        except OSError as exc:
            raise ProtocolError("disconnected") from exc
        if not part:
            return None
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def send_frame(conn: socket.socket, payload: dict) -> None:
    try:
        conn.sendall(encode(payload))
    except OSError as exc:
        raise ProtocolError("disconnected") from exc
