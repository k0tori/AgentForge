"""Unix-socket executor. This process is the only one that may open the Docker socket.

Clients do not choose container names or attempt ids used for cleanup. Closing
the connection, exceeding the timeout, or restarting this process removes the
container that this process started. A later connection cannot name somebody
else's container.
"""
from __future__ import annotations

import logging
import os
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

from src.config import settings
from src.harness.sandbox.errors import SandboxUnavailableError
from src.harness.sandbox.protocol import (
    MAX_OUTPUT_BYTES,
    MAX_REQUEST_BYTES,
    ProtocolError,
    argv_for,
    decode_request,
    read_frame,
    send_frame,
)
from src.harness.sandbox.runtime import execute_in_docker, local_docker_ready, local_image_present
from src.harness.sandbox.workspace_guard import create_attempt, open_attempt, prepare_root

logger = logging.getLogger(__name__)

_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)
_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def peer_uid(conn: socket.socket) -> int:
    raw = conn.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3I"))
    _pid, uid, _gid = struct.unpack("3I", raw)
    return int(uid)


def _claim(name: str) -> bool:
    with _ACTIVE_LOCK:
        if name in _ACTIVE:
            return False
        _ACTIVE.add(name)
        return True


def _release(name: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.discard(name)


def _ping_payload() -> dict:
    if not local_docker_ready():
        return {"ok": False, "error": "unavailable"}
    return {
        "ok": True,
        "image_present": local_image_present(settings.SANDBOX_IMAGE),
        "image": settings.SANDBOX_IMAGE,
    }


def _send_error(conn: socket.socket, reason: str) -> None:
    try:
        send_frame(conn, {"ok": False, "error": reason})
    except ProtocolError:
        return


def _handle(conn: socket.socket, root: Path) -> None:
    conn.settimeout(10)
    try:
        if peer_uid(conn) != settings.SANDBOX_API_UID:
            _send_error(conn, "unauthorized")
            return
        try:
            payload = decode_request(read_frame(conn, MAX_REQUEST_BYTES))
        except ProtocolError as exc:
            _send_error(conn, exc.reason if exc.reason != "disconnected" else "bad_request")
            return
        op = payload["op"]
        if op == "ping":
            send_frame(conn, _ping_payload())
            return
        if op == "prepare":
            try:
                name = create_attempt(root, settings.SANDBOX_API_UID, settings.SANDBOX_API_GID)
            except SandboxUnavailableError:
                logger.info("prepare refused")
                _send_error(conn, "unavailable")
                return
            send_frame(conn, {"ok": True, "name": name})
            return
        _run(conn, root, payload)
    except ProtocolError:
        return
    finally:
        conn.close()


def _run(conn: socket.socket, root: Path, payload: dict) -> None:
    name = payload["name"]
    cmd = payload["cmd"]
    if not _claim(name):
        _send_error(conn, "busy")
        return
    descriptor: int | None = None
    cancel = threading.Event()
    watcher = threading.Thread(target=_watch_disconnect, args=(conn, cancel), daemon=True)
    watcher.start()
    try:
        try:
            descriptor, workspace = open_attempt(root, name, settings.SANDBOX_API_UID, settings.SANDBOX_API_GID)
            argv = argv_for(cmd, str(payload.get("target", "")))
        except (SandboxUnavailableError, ProtocolError):
            logger.info("run refused cmd=%s", cmd)
            _send_error(conn, "unavailable" if not cancel.is_set() else "disconnected")
            return
        if cancel.is_set():
            _send_error(conn, "disconnected")
            return
        try:
            result = execute_in_docker(
                argv,
                workspace=str(workspace),
                timeout=int(payload["timeout"]),
                attempt_id=name,
                cancel_event=cancel,
                max_output=MAX_OUTPUT_BYTES,
            )
        except subprocess.TimeoutExpired:
            logger.info("run timeout cmd=%s", cmd)
            _send_error(conn, "timeout")
            return
        except SandboxUnavailableError as exc:
            reason = "disconnected" if cancel.is_set() else "unavailable"
            if getattr(exc, "code", "") == "sandbox_image_missing":
                reason = "image_missing"
            logger.info("run failed cmd=%s reason=%s", cmd, reason)
            _send_error(conn, reason)
            return
        try:
            send_frame(
                conn,
                {
                    "ok": True,
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "truncated": result.truncated,
                },
            )
        except ProtocolError:
            return
        logger.info("run finished cmd=%s exit=%s truncated=%s", cmd, result.returncode, result.truncated)
    finally:
        cancel.set()
        _release(name)
        if descriptor is not None:
            os.close(descriptor)


def _watch_disconnect(conn: socket.socket, cancel: threading.Event) -> None:
    """Closing the API connection cancels the container. It does not select another one."""
    try:
        watch = socket.fromfd(conn.fileno(), socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        cancel.set()
        return
    watch.setblocking(False)
    try:
        while not cancel.is_set():
            try:
                peeked = watch.recv(1, socket.MSG_PEEK)
            except BlockingIOError:
                cancel.wait(0.2)
                continue
            except OSError:
                cancel.set()
                return
            if peeked == b"":
                cancel.set()
                return
            cancel.wait(0.2)
    finally:
        watch.close()


def _listen(root: Path) -> None:
    path = Path(settings.SANDBOX_EXECUTOR_SOCK)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise SandboxUnavailableError("Executor socket directory is a symlink")
    if path.exists() or path.is_symlink():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    if os.geteuid() == 0:
        os.chown(path.parent, 0, settings.SANDBOX_API_GID)
        os.chmod(path.parent, 0o750)
        os.chown(path, 0, settings.SANDBOX_API_GID)
    os.chmod(path, 0o660)
    server.listen(16)
    logger.info("executor listening")
    while True:
        conn, _addr = server.accept()
        threading.Thread(target=_handle, args=(conn, root), daemon=True).start()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    try:
        root = prepare_root(
            Path(settings.SANDBOX_WORKSPACE_ROOT),
            settings.SANDBOX_API_UID,
            settings.SANDBOX_API_GID,
        )
        from src.harness.sandbox.runtime import reap_managed_containers

        reap_managed_containers()
        _listen(root)
    except SandboxUnavailableError as exc:
        logger.error("executor refused to start: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
