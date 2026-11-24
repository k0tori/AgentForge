"""Real Compose layout: the API has no Docker socket and the executor is a separate container.

These tests fail when Docker or Compose is missing. They are not replaced by reading the YAML.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.sandbox, pytest.mark.integration]

ROOT = Path(__file__).resolve().parents[2]
PROJECT = f"afm3{uuid.uuid4().hex[:6]}"
HOST_WORKSPACES = Path("/var/lib/agentforge/workspaces")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "docker-compose.yml")]


def _require_compose() -> None:
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20, check=False)
    if info.returncode != 0:
        pytest.fail(f"Docker is not available; the executor layout was not verified.\n{info.stderr}")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=20, check=False)
    if version.returncode != 0:
        pytest.fail(f"Docker Compose is not available; the executor layout was not verified.\n{version.stderr}")


def _compose(*args: str, input_text: str | None = None, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*COMPOSE, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=ROOT,
    )


def _api(script: str, *extra: str, env: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    command = [*COMPOSE, "run", "--no-deps", "--rm", "-T"]
    for item in env or []:
        command.extend(["-e", item])
    command.extend([*extra, "--entrypoint", "python", "api", "-"])
    return subprocess.run(
        command,
        input=script,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=ROOT,
    )


def _instance_ids(instance: str) -> list[str]:
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=agentforge.managed=1",
            "--filter",
            f"label=agentforge.executor={instance}",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def _managed() -> str:
    return "\n".join(_instance_ids(PROJECT))


@pytest.fixture(scope="module")
def stack():
    _require_compose()
    build = _compose("build", "sandbox", "executor", "api", timeout=1800)
    if build.returncode != 0:
        pytest.fail(f"image build failed\n{build.stderr[-4000:]}")
    up = _compose("up", "-d", "--wait", "--no-deps", "executor", timeout=180)
    if up.returncode != 0:
        pytest.fail(f"executor did not become healthy\n{up.stderr[-4000:]}\n{up.stdout[-2000:]}")
    init = _compose("run", "--no-deps", "--rm", "-T", "api-init", timeout=180)
    if init.returncode != 0:
        pytest.fail(f"task volume init failed\n{init.stderr[-2000:]}")
    try:
        yield PROJECT
    finally:
        _compose("down", "-v", "--remove-orphans", timeout=180)
        ids = _instance_ids(PROJECT)
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, check=False)


def test_api_container_cannot_open_the_host_socket(stack: str):
    del stack
    identity = _api(
        textwrap.dedent(
            """
            import os, shutil
            assert os.geteuid() == 10002, os.geteuid()
            assert os.getegid() == 10002, os.getegid()
            assert 0 not in os.getgroups()
            assert shutil.which("docker") is None
            assert not os.path.exists("/var/run/docker.sock")
            print("api-identity-ok")
            """
        )
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    assert "api-identity-ok" in identity.stdout

    mounted = _api(
        textwrap.dedent(
            """
            import os, socket
            from src.harness.sandbox import runtime as sandbox_runtime
            assert os.path.exists("/var/run/docker.sock")
            reachable = sandbox_runtime.api_can_reach_docker_socket()
            print("reachable", reachable)
            if reachable:
                assert sandbox_runtime.docker_ready() is False
                assert "not hardened" in sandbox_runtime.last_sandbox_block_reason
            else:
                probe = socket.socket(socket.AF_UNIX)
                try:
                    probe.connect("/var/run/docker.sock")
                except PermissionError:
                    print("socket-not-openable")
                else:
                    raise SystemExit("socket connect succeeded while the probe said it did not")
            print("misdeploy-ok")
            """
        ),
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
    )
    assert mounted.returncode == 0, mounted.stdout + mounted.stderr
    assert "misdeploy-ok" in mounted.stdout


def test_executor_runs_pytest_and_ruff_on_the_host_workspace(stack: str):
    del stack
    script = textwrap.dedent(
        """
        from pathlib import Path
        from src.harness.sandbox.client import prepare, run

        name = prepare()
        root = Path("/var/lib/agentforge/workspaces") / name
        (root / "marker.txt").write_text("from-api", encoding="utf-8")
        (root / "clean.py").write_text("def value() -> int:\\n    return 1\\n", encoding="utf-8")
        content = chr(10).join([
            "def test_round_trip():",
            "    import os",
            "    import socket",
            "    from pathlib import Path",
            "",
            "    text = Path('marker.txt').read_text(encoding='utf-8')",
            "    assert text == 'from-api'",
            "    assert 'DEEPSEEK_API_KEY' not in os.environ",
            "    joined = chr(10).join(os.environ.values())",
            "    assert 'host-only-secret' not in joined",
            "    assert not Path('/var/lib/agentforge/workspaces').exists()",
            "    assert not Path('/tmp/only-executor').exists()",
            "    sock = socket.socket()",
            "    sock.settimeout(2)",
            "    try:",
            "        sock.connect(('1.1.1.1', 443))",
            "    except OSError:",
            "        pass",
            "    else:",
            "        raise AssertionError('network')",
            "    Path('out.txt').write_text('from-sandbox', encoding='utf-8')",
        ])
        (root / "test_marker.py").write_text(content, encoding="utf-8")
        ran = run(name, "pytest", 60, "test_marker.py")
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert (root / "out.txt").read_text(encoding="utf-8") == "from-sandbox"
        lint = run(name, "ruff", 30)
        assert lint.returncode == 0, lint.stdout + lint.stderr
        print("NAME", name)
        """
    )
    decoy = _compose("exec", "-T", "executor", "sh", "-c", "echo decoy > /tmp/only-executor")
    assert decoy.returncode == 0, decoy.stderr
    ran = _api(script, env=["DEEPSEEK_API_KEY=host-only-secret"])
    assert ran.returncode == 0, ran.stdout + ran.stderr
    name = [line.split()[1] for line in ran.stdout.splitlines() if line.startswith("NAME ")][-1]
    assert len(name) == 32
    host = subprocess.run(
        [
            "docker", "run", "--rm", "--user", "0", "-v", f"{HOST_WORKSPACES}:/w", "agentforge-sandbox:latest",
            "python", "-c",
            (
                "from pathlib import Path; "
                f"p=Path('/w/{name}'); "
                "print(p.joinpath('marker.txt').read_text()); "
                "print(p.joinpath('out.txt').read_text()); "
                "print(p.joinpath('out.txt').stat().st_uid)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert host.returncode == 0, host.stderr
    lines = host.stdout.strip().splitlines()
    assert lines[:3] == ["from-api", "from-sandbox", "10002"]
    assert _managed() == ""


def test_executor_rejects_escape_identity_and_cleans_up(stack: str):
    del stack
    script = textwrap.dedent(
        """
        import json, socket, threading, time
        from pathlib import Path
        from src.harness.sandbox.client import prepare

        def frame(payload):
            body = json.dumps(payload).encode()
            return len(body).to_bytes(4, "big") + body

        def talk(payload):
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(20)
            sock.connect("/run/agentforge/executor.sock")
            sock.sendall(frame(payload))
            size = int.from_bytes(sock.recv(4), "big")
            return sock, json.loads(sock.recv(size).decode())

        name = prepare()
        root = Path("/var/lib/agentforge/workspaces") / name
        (root / "test_sleep.py").write_text("import time\\ndef test_sleep():\\n    time.sleep(30)\\n")
        bad_op, bad_op_body = talk({"op": "cleanup", "name": name})
        bad_op.close()
        assert bad_op_body["error"] == "bad_op"
        bad_field, bad_field_body = talk({"op": "run", "name": name, "cmd": "pytest", "timeout": 5, "image": "alpine"})
        bad_field.close()
        assert bad_field_body["error"] == "bad_field"
        bad_cmd, bad_cmd_body = talk({"op": "run", "name": name, "cmd": "bash", "timeout": 5})
        bad_cmd.close()
        assert bad_cmd_body["error"] == "bad_command"
        link = prepare()
        print("link", link)
        print("sleep", name)
        """
    )
    prepared = _api(script)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    link = ""
    sleep_name = ""
    for line in prepared.stdout.splitlines():
        if line.startswith("link "):
            link = line.split()[1]
        if line.startswith("sleep "):
            sleep_name = line.split()[1]
    swapped = subprocess.run(
        [
            "docker", "run", "--rm", "--user", "0", "-v", f"{HOST_WORKSPACES}:/w", "agentforge-sandbox:latest",
            "python", "-c",
            f"import os, shutil; shutil.rmtree('/w/{link}'); os.symlink('/etc', '/w/{link}')",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert swapped.returncode == 0, swapped.stderr
    refused = _api(
        textwrap.dedent(
            f"""
            from src.harness.sandbox.client import run
            try:
                run({link!r}, "pytest", 10, "test_sleep.py")
            except Exception as exc:
                print(type(exc).__name__)
            else:
                raise SystemExit("symlink was executed")
            """
        )
    )
    assert refused.returncode == 0, refused.stdout + refused.stderr
    assert "SandboxUnavailableError" in refused.stdout

    wrong = subprocess.run(
        [
            "docker", "run", "--rm", "--user", "10003:10003", "--group-add", "10002",
            "-v", f"{PROJECT}_executor-sock:/run/agentforge",
            "python:3.12-slim-bookworm", "python", "-c",
            (
                "import json,socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); "
                "s.connect('/run/agentforge/executor.sock'); "
                "body=json.dumps({'op':'prepare'}).encode(); "
                "s.sendall(len(body).to_bytes(4,'big')+body); "
                "n=int.from_bytes(s.recv(4),'big'); print(s.recv(n).decode())"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert wrong.returncode == 0, wrong.stderr
    assert json.loads(wrong.stdout)["error"] == "unauthorized"

    busy = _api(
        textwrap.dedent(
            f"""
            import json, socket, threading, time
            name = {sleep_name!r}

            def frame(payload):
                body = json.dumps(payload).encode()
                return len(body).to_bytes(4, "big") + body

            def once(timeout):
                sock = socket.socket(socket.AF_UNIX)
                sock.settimeout(timeout + 5)
                sock.connect("/run/agentforge/executor.sock")
                payload = {{"op": "run", "name": name, "cmd": "pytest", "timeout": timeout, "target": "test_sleep.py"}}
                sock.sendall(frame(payload))
                size = int.from_bytes(sock.recv(4), "big")
                return json.loads(sock.recv(size).decode())

            holder = {{}}
            def hold():
                try:
                    holder["body"] = once(15)
                except Exception as exc:
                    holder["body"] = {{"error": type(exc).__name__}}
            thread = threading.Thread(target=hold)
            thread.start()
            time.sleep(1)
            second = once(15)
            thread.join(timeout=20)
            print("second", second.get("error"))
            """
        )
    )
    assert busy.returncode == 0, busy.stdout + busy.stderr
    assert "second busy" in busy.stdout
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _managed():
        time.sleep(0.5)
    assert _managed() == ""

    timed = _api(
        textwrap.dedent(
            f"""
            import json, socket, time
            name = {sleep_name!r}
            payload = {{"op": "run", "name": name, "cmd": "pytest", "timeout": 2, "target": "test_sleep.py"}}
            body = json.dumps(payload).encode()
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(15)
            started = time.monotonic()
            sock.connect("/run/agentforge/executor.sock")
            sock.sendall(len(body).to_bytes(4, "big") + body)
            size = int.from_bytes(sock.recv(4), "big")
            payload = json.loads(sock.recv(size).decode())
            print("elapsed", round(time.monotonic() - started, 2))
            print("error", payload.get("error"))
            """
        )
    )
    assert timed.returncode == 0, timed.stdout + timed.stderr
    assert "error timeout" in timed.stdout
    elapsed = float([line for line in timed.stdout.splitlines() if line.startswith("elapsed ")][-1].split()[1])
    assert elapsed < 15
    assert _managed() == ""

    dropped = _api(
        textwrap.dedent(
            f"""
            import json, socket, time
            name = {sleep_name!r}
            payload = {{"op": "run", "name": name, "cmd": "pytest", "timeout": 30, "target": "test_sleep.py"}}
            body = json.dumps(payload).encode()
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(5)
            sock.connect("/run/agentforge/executor.sock")
            sock.sendall(len(body).to_bytes(4, "big") + body)
            time.sleep(1.5)
            sock.close()
            print("closed")
            """
        )
    )
    assert dropped.returncode == 0, dropped.stdout + dropped.stderr
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _managed():
        time.sleep(0.5)
    assert _managed() == ""


def _running(container_id: str) -> bool:
    probe = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def _wait_until(predicate, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.4)
    return False


def test_restart_reaps_only_this_executor_instance(stack: str):
    """Two installations. Restarting A removes A's leftover container and leaves B running."""
    del stack
    suffix = uuid.uuid4().hex[:8]
    id_a = f"inst-a-{suffix}"
    id_b = f"inst-b-{suffix}"
    base = Path("/tmp") / f"agentforge-ns-{suffix}"
    names = {
        "exec_a": f"af-exec-a-{suffix}",
        "exec_b": f"af-exec-b-{suffix}",
        "client_a": f"af-client-a-{suffix}",
        "client_b": f"af-client-b-{suffix}",
    }
    roots = {"a": base / "a" / "var" / "lib" / "workspaces", "b": base / "b" / "var" / "lib" / "workspaces"}
    socks = {"a": base / "a" / "sock", "b": base / "b" / "sock"}

    def start_executor(key: str, instance: str) -> None:
        roots[key].mkdir(parents=True, exist_ok=True)
        socks[key].mkdir(parents=True, exist_ok=True)
        started = subprocess.run(
            [
                "docker", "run", "-d", "--name", names[f"exec_{key}"],
                "--user", "0:0",
                "-e", f"SANDBOX_EXECUTOR_ID={instance}",
                "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
                "-e", f"SANDBOX_WORKSPACE_ROOT={roots[key]}",
                "-e", "SANDBOX_API_UID=10002",
                "-e", "SANDBOX_API_GID=10002",
                "-e", "SANDBOX_RUN_UID=10002",
                "-e", "SANDBOX_RUN_GID=10002",
                "-e", "SANDBOX_IMAGE=agentforge-sandbox:latest",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{roots[key]}:{roots[key]}",
                "-v", f"{socks[key]}:/run/agentforge",
                "agentforge-executor:latest",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert started.returncode == 0, started.stderr
        logs = ""

        def listening() -> bool:
            nonlocal logs
            probe = subprocess.run(
                ["docker", "logs", names[f"exec_{key}"]],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            logs = probe.stdout + probe.stderr
            return "executor listening" in logs

        assert _wait_until(listening, 20), logs

    def start_client(key: str) -> None:
        script = textwrap.dedent(
            """
            import os
            from pathlib import Path
            from src.harness.sandbox.client import prepare, run
            name = prepare()
            target = Path(os.environ["SANDBOX_WORKSPACE_ROOT"]) / name
            target.joinpath("test_sleep.py").write_text(
                "import time\\n\\ndef test_sleep():\\n    time.sleep(50)\\n",
                encoding="utf-8",
            )
            print("READY", flush=True)
            run(name, "pytest", 50, "test_sleep.py")
            """
        )
        started = subprocess.run(
            [
                "docker", "run", "-d", "--name", names[f"client_{key}"],
                "--user", "10002:10002",
                "--entrypoint", "python",
                "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
                "-e", f"SANDBOX_WORKSPACE_ROOT={roots[key]}",
                "-v", f"{roots[key]}:{roots[key]}",
                "-v", f"{socks[key]}:/run/agentforge",
                "agentforge-executor:latest",
                "-c", script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert started.returncode == 0, started.stderr

    try:
        start_executor("a", id_a)
        start_executor("b", id_b)
        start_client("a")
        start_client("b")
        assert _wait_until(lambda: len(_instance_ids(id_a)) == 1, 40), _instance_ids(id_a)
        assert _wait_until(lambda: len(_instance_ids(id_b)) == 1, 40), _instance_ids(id_b)
        left_a = _instance_ids(id_a)[0]
        left_b = _instance_ids(id_b)[0]
        killed = subprocess.run(
            ["docker", "kill", names["exec_a"]],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert killed.returncode == 0, killed.stderr
        time.sleep(1)
        assert _running(left_a), "A's sandbox exited when its executor was killed, so reap was not tested"
        assert _running(left_b)
        subprocess.run(["docker", "rm", "-f", names["exec_a"]], capture_output=True, check=False)
        start_executor("a", id_a)
        assert _wait_until(lambda: left_a not in _instance_ids(id_a) and not _running(left_a), 20)
        assert _running(left_b)
        assert left_b in _instance_ids(id_b)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", *names.values()],
            capture_output=True,
            check=False,
        )
        leftovers = _instance_ids(id_a) + _instance_ids(id_b)
        if leftovers:
            subprocess.run(["docker", "rm", "-f", *leftovers], capture_output=True, check=False)
        subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{base}:/cleanup",
                "agentforge-sandbox:latest",
                "python", "-c",
                "import shutil; shutil.rmtree('/cleanup', ignore_errors=True)",
            ],
            capture_output=True,
            check=False,
        )


def _start_named_executor(name: str, instance: str, root: Path, sock: Path, image: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    sock.mkdir(parents=True, exist_ok=True)
    started = subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "--user", "0:0",
            "-e", f"SANDBOX_EXECUTOR_ID={instance}",
            "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
            "-e", f"SANDBOX_WORKSPACE_ROOT={root}",
            "-e", "SANDBOX_API_UID=10002",
            "-e", "SANDBOX_API_GID=10002",
            "-e", "SANDBOX_RUN_UID=10002",
            "-e", "SANDBOX_RUN_GID=10002",
            "-e", f"SANDBOX_IMAGE={image}",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{root}:{root}",
            "-v", f"{sock}:/run/agentforge",
            "agentforge-executor:latest",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert started.returncode == 0, started.stderr
    logs = ""

    def listening() -> bool:
        nonlocal logs
        probe = subprocess.run(
            ["docker", "logs", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        logs = probe.stdout + probe.stderr
        return "executor listening" in logs

    assert _wait_until(listening, 20), logs


def _raw_pytest(root: Path, sock: Path) -> subprocess.CompletedProcess[str]:
    """Send prepare and a real pytest run. Prints `RUN <error-or-exit>`."""
    script = textwrap.dedent(
        """
        import json, os, socket
        from pathlib import Path

        def talk(payload):
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(30)
            sock.connect("/run/agentforge/executor.sock")
            body = json.dumps(payload).encode()
            sock.sendall(len(body).to_bytes(4, "big") + body)
            size = int.from_bytes(sock.recv(4), "big")
            data = b""
            while len(data) < size:
                chunk = sock.recv(size - len(data))
                if not chunk:
                    break
                data += chunk
            sock.close()
            return json.loads(data.decode())

        prepared = talk({"op": "prepare"})
        if not prepared.get("ok"):
            print("RUN", prepared.get("error"))
            raise SystemExit(0)
        name = prepared["name"]
        target = Path(os.environ["SANDBOX_WORKSPACE_ROOT"]) / name
        target.joinpath("test_missing.py").write_text(
            "def test_missing():\\n    assert True\\n",
            encoding="utf-8",
        )
        ran = talk({
            "op": "run",
            "name": name,
            "cmd": "pytest",
            "timeout": 30,
            "target": "test_missing.py",
        })
        if ran.get("ok"):
            print("RUN exit", ran.get("exit_code"))
        else:
            print("RUN", ran.get("error"))
        """
    )
    return subprocess.run(
        [
            "docker", "run", "--rm", "--user", "10002:10002",
            "--entrypoint", "python",
            "-v", f"{root}:{root}",
            "-v", f"{sock}:/run/agentforge",
            "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
            "-e", f"SANDBOX_WORKSPACE_ROOT={root}",
            "agentforge-executor:latest",
            "-c", script,
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


# Documented command is `docker compose up -d --build`.
# Naming executor still keeps its default depends_on (sandbox) and does not pass --no-deps.
# postgres, redis, and api are not started, so this is not an unfiltered full-stack boot.
_DOCUMENTED_UP = ("up", "-d", "--build", "executor")


def _compose_project(project: str, *args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=ROOT,
    )


def _service_id(project: str, service: str) -> str:
    listed = _compose_project(project, "ps", "-aq", service, timeout=30)
    assert listed.returncode == 0, listed.stderr
    return listed.stdout.strip().splitlines()[-1] if listed.stdout.strip() else ""


def _inspect(container_id: str, template: str) -> str:
    probe = subprocess.run(
        ["docker", "inspect", "-f", template, container_id],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout.strip()


def test_documented_up_build_runs_executor_after_sandbox():
    """`docker compose up -d --build executor` includes the default sandbox dependency."""
    _require_compose()
    project = f"m4up{uuid.uuid4().hex[:6]}"
    argv = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.yml"), *_DOCUMENTED_UP]
    assert "--no-deps" not in argv
    assert tuple(argv[-4:]) == _DOCUMENTED_UP
    try:
        up = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False, cwd=ROOT)
        assert up.returncode == 0, up.stderr[-3000:]
        sandbox_id = _service_id(project, "sandbox")
        executor_id = _service_id(project, "executor")
        assert sandbox_id and executor_id
        assert _inspect(sandbox_id, "{{.State.Status}}") == "exited"
        assert _inspect(sandbox_id, "{{.State.ExitCode}}") == "0"
        assert _inspect(sandbox_id, "{{.State.FinishedAt}}") <= _inspect(executor_id, "{{.State.StartedAt}}")

        def healthy() -> bool:
            return _inspect(executor_id, "{{.State.Health.Status}}") == "healthy"

        assert _wait_until(healthy, 40), _inspect(executor_id, "{{.State.Status}}")
        ran = subprocess.run(
            [
                "docker", "run", "--rm", "--user", "10002:10002",
                "--entrypoint", "python",
                "-v", "/var/lib/agentforge/workspaces:/var/lib/agentforge/workspaces",
                "-v", f"{project}_executor-sock:/run/agentforge",
                "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
                "-e", "SANDBOX_WORKSPACE_ROOT=/var/lib/agentforge/workspaces",
                "agentforge-executor:latest",
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "from src.harness.sandbox.client import prepare, run; "
                    "name=prepare(); "
                    "target=Path(os.environ['SANDBOX_WORKSPACE_ROOT'])/name; "
                    "target.joinpath('test_chain.py').write_text("
                    "'def test_chain():\\n    assert True\\n', encoding='utf-8'); "
                    "result=run(name, 'pytest', 60, 'test_chain.py'); "
                    "raise SystemExit(result.returncode)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
    finally:
        _compose_project(project, "down", "--remove-orphans", timeout=180)
        leftovers = _instance_ids(project)
        if leftovers:
            subprocess.run(["docker", "rm", "-f", *leftovers], capture_output=True, check=False)


def test_failed_sandbox_does_not_make_executor_ready(tmp_path: Path):
    """A failed sandbox dependency must not leave the executor running."""
    _require_compose()
    project = f"m4bad{uuid.uuid4().hex[:6]}"
    override = tmp_path / "sandbox-fail.yml"
    override.write_text("services:\n  sandbox:\n    entrypoint: ['false']\n", encoding="utf-8")
    argv = [
        "docker", "compose", "-p", project,
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(override),
        *_DOCUMENTED_UP,
    ]
    assert "--no-deps" not in argv
    try:
        up = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False, cwd=ROOT)
        assert up.returncode != 0, up.stdout + up.stderr
        running = _compose_project(project, "ps", "--status", "running", "--services", timeout=30)
        assert "executor" not in running.stdout.split()
        executor_id = _service_id(project, "executor")
        if executor_id:
            assert _inspect(executor_id, "{{.State.Running}}") == "false"
            health = _inspect(executor_id, "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}")
            assert health != "healthy"
    finally:
        _compose_project(project, "down", "--remove-orphans", timeout=180)


def test_missing_image_is_not_pulled(stack: str):
    del stack
    suffix = uuid.uuid4().hex[:8]
    tag = f"agentforge-absent-{suffix}:local"
    instance = f"missing-{suffix}"
    name = f"af-missing-{suffix}"
    base = Path("/tmp") / f"agentforge-missing-{suffix}"
    root = base / "var" / "lib" / "workspaces"
    sock = base / "sock"
    try:
        _start_named_executor(name, instance, root, sock, tag)
        client = _raw_pytest(root, sock)
        assert client.returncode == 0, client.stdout + client.stderr
        assert "RUN image_missing" in client.stdout
        assert "passed" not in client.stdout.lower()
        logs = subprocess.run(
            ["docker", "logs", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert "was not pulled" in (logs.stdout + logs.stderr)
        absent = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert absent.returncode != 0
        assert _instance_ids(instance) == []
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        leftovers = _instance_ids(instance)
        if leftovers:
            subprocess.run(["docker", "rm", "-f", *leftovers], capture_output=True, check=False)
        subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{base}:/cleanup", "agentforge-sandbox:latest",
                "python", "-c", "import shutil; shutil.rmtree('/cleanup', ignore_errors=True)",
            ],
            capture_output=True,
            check=False,
        )


def test_deleted_pinned_image_id_fails_a_real_run(stack: str):
    """Remove only an isolated image id. Do not delete agentforge-sandbox:latest."""
    del stack
    suffix = uuid.uuid4().hex[:8]
    tag = f"agentforge-iso-{suffix}:local"
    instance = f"iso-{suffix}"
    name = f"af-iso-{suffix}"
    base = Path("/tmp") / f"agentforge-iso-{suffix}"
    root = base / "var" / "lib" / "workspaces"
    sock = base / "sock"
    dockerfile = base / "Dockerfile"
    image_id = ""
    try:
        base.mkdir(parents=True, exist_ok=True)
        dockerfile.write_text(
            "FROM python:3.12-slim-bookworm\nRUN echo isolated > /opt/isolated.txt\n",
            encoding="utf-8",
        )
        built = subprocess.run(
            ["docker", "build", "-t", tag, str(base)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert built.returncode == 0, built.stderr[-2000:]
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert inspected.returncode == 0, inspected.stderr
        image_id = inspected.stdout.strip()
        _start_named_executor(name, instance, root, sock, tag)
        removed = subprocess.run(
            ["docker", "rmi", tag],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert removed.returncode == 0, removed.stderr
        gone = subprocess.run(
            ["docker", "image", "inspect", image_id],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert gone.returncode != 0, "isolated image id is still present; refusing to delete other tags"
        client = _raw_pytest(root, sock)
        assert client.returncode == 0, client.stdout + client.stderr
        assert "RUN image_missing" in client.stdout
        assert "passed" not in client.stdout.lower()
        assert _instance_ids(instance) == []
        latest = subprocess.run(
            ["docker", "image", "inspect", "agentforge-sandbox:latest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert latest.returncode == 0
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        leftovers = _instance_ids(instance)
        if leftovers:
            subprocess.run(["docker", "rm", "-f", *leftovers], capture_output=True, check=False)
        if image_id:
            subprocess.run(["docker", "rmi", image_id], capture_output=True, check=False)
        subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{base}:/cleanup", "agentforge-sandbox:latest",
                "python", "-c", "import shutil; shutil.rmtree('/cleanup', ignore_errors=True)",
            ],
            capture_output=True,
            check=False,
        )


def test_pinned_digest_ignores_a_retagged_latest(stack: str):
    del stack
    original = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "agentforge-sandbox:latest"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert original.returncode == 0, original.stderr
    original_id = original.stdout.strip()
    suffix = uuid.uuid4().hex[:8]
    instance = f"pin-{suffix}"
    name = f"af-pin-{suffix}"
    base = Path("/tmp") / f"agentforge-pin-{suffix}"
    root = base / "var" / "lib" / "workspaces"
    sock = base / "sock"
    try:
        _start_named_executor(name, instance, root, sock, "agentforge-sandbox:latest")
        retag = subprocess.run(
            ["docker", "tag", "python:3.12-slim-bookworm", "agentforge-sandbox:latest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert retag.returncode == 0, retag.stderr
        decoy = subprocess.run(
            [
                "docker", "run", "--rm", "--pull", "never", "--entrypoint", "python",
                "agentforge-sandbox:latest", "-c", "import pytest",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert decoy.returncode != 0
        client = subprocess.run(
            [
                "docker", "run", "--rm", "--user", "10002:10002",
                "--entrypoint", "python",
                "-v", f"{root}:{root}",
                "-v", f"{sock}:/run/agentforge",
                "-e", "SANDBOX_EXECUTOR_SOCK=/run/agentforge/executor.sock",
                "-e", f"SANDBOX_WORKSPACE_ROOT={root}",
                "agentforge-executor:latest",
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "from src.harness.sandbox.client import prepare, run; "
                    "name=prepare(); "
                    "target=Path(os.environ['SANDBOX_WORKSPACE_ROOT'])/name; "
                    "target.joinpath('test_pin.py').write_text("
                    "'def test_pin():\\n    import pytest\\n    assert pytest.__doc__ is not None\\n',"
                    "encoding='utf-8'); "
                    "result=run(name,'pytest',60,'test_pin.py'); "
                    "raise SystemExit(result.returncode)"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert client.returncode == 0, client.stdout + client.stderr
    finally:
        subprocess.run(
            ["docker", "tag", original_id, "agentforge-sandbox:latest"],
            capture_output=True,
            check=False,
        )
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        leftovers = _instance_ids(instance)
        if leftovers:
            subprocess.run(["docker", "rm", "-f", *leftovers], capture_output=True, check=False)
        subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0",
                "-v", f"{base}:/cleanup", "agentforge-sandbox:latest",
                "python", "-c", "import shutil; shutil.rmtree('/cleanup', ignore_errors=True)",
            ],
            capture_output=True,
            check=False,
        )


def test_task_database_volume_is_writable_by_the_api_user(stack: str):
    del stack
    write = _api("open('/var/lib/agentforge/tasks.sqlite','w',encoding='utf-8').write('persisted')\n")
    assert write.returncode == 0, write.stdout + write.stderr
    read = _api("print(open('/var/lib/agentforge/tasks.sqlite',encoding='utf-8').read())\n")
    assert read.returncode == 0, read.stdout + read.stderr
    assert read.stdout.strip() == "persisted"
