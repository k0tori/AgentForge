"""Documented full-stack boot: `docker compose up -d --build` with no service name.

This is not a config-file assertion. It fails when Docker or Compose is missing.
It does not use --no-deps and does not omit the API or the databases.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.stack, pytest.mark.integration]

ROOT = Path(__file__).resolve().parents[2]
KEY = "m5-stack-test-key"
DOCUMENTED = ("docker", "compose", "up", "-d", "--build")


def _require_compose() -> None:
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20, check=False)
    if info.returncode != 0:
        pytest.fail(f"Docker is not available; the documented stack was not started.\n{info.stderr}")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=20, check=False)
    if version.returncode != 0:
        pytest.fail(f"Docker Compose is not available; the documented stack was not started.\n{version.stderr}")


def _env(demo: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("DETERMINISTIC_EXECUTOR", None)
    env["API_KEY"] = KEY
    if demo:
        env["DETERMINISTIC_EXECUTOR"] = "1"
    return env


def _compose(project: str, *args: str, env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=ROOT,
        env=env,
    )


def _http(method: str, path: str, body: dict | None = None, key: str | None = KEY) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-API-Key"] = key
    request = urllib.request.Request(f"http://127.0.0.1:8000{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_documented_compose_up_serves_demo_and_keeps_tasks():
    """Run the documented command, then the explicit demo override, both without a service name."""
    _require_compose()
    project = f"m5stack{uuid.uuid4().hex[:6]}"
    started = time.monotonic()
    demo_env = _env(True)
    plain_env = _env(False)
    try:
        argv = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), "up", "-d", "--build"]
        assert "--no-deps" not in argv
        assert argv[-3:] == ["up", "-d", "--build"]
        print("COMMAND", " ".join(DOCUMENTED), "project", project)
        up = subprocess.run(
            argv, capture_output=True, text=True, timeout=1800, check=False, cwd=ROOT, env=plain_env,
        )
        print("UP_EXIT", up.returncode)
        print(up.stdout[-2000:])
        print(up.stderr[-2000:])
        assert up.returncode == 0, up.stderr[-3000:]
        print("UP_SECONDS", round(time.monotonic() - started, 1))

        sandbox = _compose(project, "ps", "-aq", "sandbox", env=plain_env, timeout=30)
        executor = _compose(project, "ps", "-aq", "executor", env=plain_env, timeout=30)
        api = _compose(project, "ps", "-aq", "api", env=plain_env, timeout=30)
        postgres = _compose(project, "ps", "-aq", "postgres", env=plain_env, timeout=30)
        redis = _compose(project, "ps", "-aq", "redis", env=plain_env, timeout=30)
        assert all(item.stdout.strip() for item in (sandbox, executor, api, postgres, redis))
        sandbox_id = sandbox.stdout.strip()
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", sandbox_id],
            capture_output=True, text=True, timeout=20, check=False,
        )
        print("SANDBOX", inspect.stdout.strip())
        assert inspect.stdout.strip() == "exited 0"

        deadline = time.monotonic() + 180
        health: dict = {}
        while time.monotonic() < deadline:
            try:
                status, raw = _http("GET", "/api/v1/health", key=None)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                time.sleep(2)
                continue
            if status == 200:
                health = json.loads(raw)
                print("HEALTH", raw)
                if health.get("postgres") is True and health.get("redis") is True:
                    break
            time.sleep(2)
        else:
            pytest.fail(f"API health did not report postgres and redis: {health}")
        assert health["status"] == "healthy"
        assert health["postgres"] is True
        assert health["redis"] is True

        identity = _compose(project, "exec", "-T", "api", "id", "-u", env=plain_env, timeout=30)
        assert identity.stdout.strip() == "10002"
        api_mounts = subprocess.run(
            ["docker", "inspect", "-f", "{{json .Mounts}}", api.stdout.strip()],
            capture_output=True, text=True, timeout=20, check=False,
        )
        assert "docker.sock" not in api_mounts.stdout
        exec_mounts = subprocess.run(
            ["docker", "inspect", "-f", "{{json .Mounts}}", executor.stdout.strip()],
            capture_output=True, text=True, timeout=20, check=False,
        )
        assert "docker.sock" in exec_mounts.stdout

        denied = _http(
            "POST",
            "/api/v1/tasks/",
            {"intent": "Add a tag without a key", "codebase": {"type": "local_path", "path": "/app/toy-repo"}},
            key=None,
        )
        assert denied[0] == 401

        created_status, created_raw = _http(
            "POST",
            "/api/v1/tasks/",
            {"intent": "Add a tag without a key", "codebase": {"type": "local_path", "path": "/app/toy-repo"}},
        )
        assert created_status == 202, created_raw
        failed_id = json.loads(created_raw)["task_id"]
        failed = _wait_task(failed_id)
        print("PGE", json.dumps(failed))
        assert failed["status"] == "failed"
        assert failed["error_code"] == "missing_model_config"
        stream = _read_sse(f"/api/v1/tasks/{failed_id}/stream")
        print("SSE", stream[:500])
        assert "missing_model_config" in stream
        assert "task_complete" in stream

        again = subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), "up", "-d", "--build"],
            capture_output=True, text=True, timeout=1800, check=False, cwd=ROOT, env=demo_env,
        )
        assert again.returncode == 0, again.stderr[-2000:]
        assert "--no-deps" not in again.args
        _wait_health()
        prepare = _compose(
            project, "exec", "-T", "-u", "10002", "api", "bash", "-c",
            "rm -rf /app/.agentforge/work/toy-copy && "
            "cp -a /app/toy-repo /app/.agentforge/work/toy-copy && "
            "git -C /app/.agentforge/work/toy-copy init -b main && "
            "git -C /app/.agentforge/work/toy-copy -c user.email=you@example.com -c user.name=You add -A && "
            "git -C /app/.agentforge/work/toy-copy -c user.email=you@example.com -c user.name=You "
            "commit -m 'demo baseline'",
            env=demo_env,
            timeout=120,
        )
        assert prepare.returncode == 0, prepare.stderr[-2000:]
        before = _compose(
            project, "exec", "-T", "-u", "10002", "api",
            "git", "-C", "/app/.agentforge/work/toy-copy", "rev-parse", "HEAD",
            env=demo_env, timeout=30,
        )
        head_before = before.stdout.strip()

        demo_status, demo_raw = _http(
            "POST",
            "/api/v1/tasks/",
            {
                "intent": "Add a ping helper without a model",
                "codebase": {"type": "local_path", "path": "/app/.agentforge/work/toy-copy"},
                "options": {
                    "executor": "deterministic",
                    "idempotency_key": "ping-once",
                    "change": {"path": "src/ping.py", "content": "def ping() -> str:\n    return \"pong\"\n"},
                },
            },
        )
        assert demo_status == 202, demo_raw
        demo_id = json.loads(demo_raw)["task_id"]
        demo = _wait_task(demo_id)
        print("DEMO", json.dumps({k: demo.get(k) for k in ("status", "error_code", "result")}))
        assert demo["status"] == "completed"
        result = demo["result"]
        assert result["demo_completed"] is True
        assert result["acceptance"] == "not_run"
        assert result["final_verdict"] is None
        assert "def ping" in result["code_diff"]
        branch = result["result_branch"]
        assert branch.startswith("agentforge/")
        shown = _compose(
            project, "exec", "-T", "-u", "10002", "api",
            "git", "-C", "/app/.agentforge/work/toy-copy", "show", f"{branch}:src/ping.py",
            env=demo_env, timeout=30,
        )
        assert shown.returncode == 0, shown.stderr
        assert "return \"pong\"" in shown.stdout
        worktree = _compose(
            project, "exec", "-T", "-u", "10002", "api",
            "bash", "-c", "test ! -e /app/.agentforge/work/toy-copy/src/ping.py",
            env=demo_env, timeout=30,
        )
        assert worktree.returncode == 0
        head_after = _compose(
            project, "exec", "-T", "-u", "10002", "api",
            "git", "-C", "/app/.agentforge/work/toy-copy", "rev-parse", "HEAD",
            env=demo_env, timeout=30,
        )
        assert head_after.stdout.strip() == head_before

        old_api = _compose(project, "ps", "-q", "api", env=demo_env, timeout=30).stdout.strip()
        recreated = subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"),
             "up", "-d", "--force-recreate", "api"],
            capture_output=True, text=True, timeout=600, check=False, cwd=ROOT, env=demo_env,
        )
        assert recreated.returncode == 0, recreated.stderr[-2000:]
        assert "--no-deps" not in recreated.args
        new_api = ""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            new_api = _compose(project, "ps", "-q", "api", env=demo_env, timeout=30).stdout.strip()
            if new_api and new_api != old_api:
                try:
                    status, raw = _http("GET", "/api/v1/health")
                except OSError:
                    time.sleep(2)
                    continue
                if status == 200 and json.loads(raw).get("postgres") is True:
                    break
            time.sleep(2)
        else:
            pytest.fail("recreated API did not become ready")
        assert new_api != old_api
        status, raw = _http("GET", f"/api/v1/tasks/{demo_id}")
        restored = json.loads(raw)
        print("RESTORED", status, restored.get("status"), new_api)
        assert status == 200
        assert restored["status"] == "completed"
        assert restored["result"]["result_branch"] == branch
    finally:
        subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"),
             "down", "-v", "--remove-orphans"],
            capture_output=True, text=True, timeout=180, check=False, cwd=ROOT,
        )


def _read_sse(path: str) -> str:
    """Read the terminal SSE payload. A closed stream is not a host fallback."""
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=20)
    try:
        conn.request("GET", path, headers={"X-API-Key": KEY, "Accept": "text/event-stream"})
        response = conn.getresponse()
        chunks: list[bytes] = []
        while True:
            try:
                piece = response.read(512)
            except OSError:
                break
            if not piece:
                break
            chunks.append(piece)
            if b"task_complete" in b"".join(chunks):
                break
        return b"".join(chunks).decode(errors="replace")
    finally:
        conn.close()


def _wait_health() -> None:
    deadline = time.monotonic() + 180
    last = ""
    while time.monotonic() < deadline:
        try:
            status, raw = _http("GET", "/api/v1/health")
        except OSError:
            time.sleep(2)
            continue
        last = raw
        if status == 200:
            body = json.loads(raw)
            if body.get("postgres") is True and body.get("redis") is True and body.get("status") == "healthy":
                return
        time.sleep(2)
    pytest.fail(f"API health stayed unavailable or degraded: {last}")


def _wait_task(task_id: str) -> dict:
    deadline = time.monotonic() + 180
    last = ""
    while time.monotonic() < deadline:
        status, raw = _http("GET", f"/api/v1/tasks/{task_id}")
        assert status == 200, raw
        last = raw
        body = json.loads(raw)
        if body["status"] in {"completed", "failed", "interrupted"}:
            return body
        time.sleep(1)
    pytest.fail(f"task did not finish: {last}")
    return {}
