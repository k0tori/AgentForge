"""Publish must keep unrelated tracked files and must not write secrets or follow links."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.api.task_manager import TaskManager, TaskStatus
from src.harness.deadline import DeadlineExceededError
from src.harness.publish_guard import PublishRejectedError
from src.harness.workspace import SprintWorkspace
from src.llm.redact import sanitize, sanitize_delivery

SECRET = "super-secret-value"
KEY = "sk-injected-secret-value"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@local",
            "-c",
            "user.name=test",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _commit_all(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")


def test_publish_keeps_tracked_hidden_directories(tmp_path: Path):
    repo = tmp_path / "repo"
    workflow = repo / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all(repo)
    workspace = SprintWorkspace(str(repo), "keep-hidden", 1)
    root = Path(workspace.create())
    (root / "src").mkdir()
    (root / "src" / "ping.py").write_text("def ping():\n    return 1\n", encoding="utf-8")
    branch = workspace.publish()
    diff = _git(repo, "diff", "--name-status", "HEAD", branch)
    assert "D\t.github/workflows/ci.yml" not in diff.stdout
    assert ".github/workflows/ci.yml" not in diff.stdout
    assert "src/ping.py" in diff.stdout
    assert _git(repo, "show", f"{branch}:.github/workflows/ci.yml").stdout == "name: CI\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "readme\n"


def test_head_symlink_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("ORIGINAL-OUTSIDE", encoding="utf-8")
    repo = tmp_path / "repo"
    source = repo / "src"
    source.mkdir(parents=True)
    link = source / "ping.py"
    link.symlink_to(outside)
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all(repo)
    link.unlink()
    link.write_text("regular-in-worktree\n", encoding="utf-8")
    workspace = SprintWorkspace(str(repo), "no-follow", 1)
    root = Path(workspace.create())
    (root / "src" / "ping.py").write_text("SPRINT-CONTENT\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError):
        workspace.publish()
    assert outside.read_text(encoding="utf-8") == "ORIGINAL-OUTSIDE"


def test_head_directory_symlink_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "kept.txt").write_text("ORIGINAL-OUTSIDE", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    linked = repo / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all(repo)
    linked.unlink()
    linked.mkdir()
    (linked / "pwn.txt").write_text("from-worktree\n", encoding="utf-8")
    workspace = SprintWorkspace(str(repo), "no-dir-link", 1)
    root = Path(workspace.create())
    (root / "linked" / "pwn.txt").write_text("SPRINT-CONTENT\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError):
        workspace.publish()
    assert (outside / "kept.txt").read_text(encoding="utf-8") == "ORIGINAL-OUTSIDE"
    assert not (outside / "pwn.txt").exists()


def test_untracked_env_is_not_committed_and_new_secret_is_refused(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all(repo)
    (repo / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    workspace = SprintWorkspace(str(repo), "skip-env", 1)
    root = Path(workspace.create())
    (root / "src").mkdir()
    (root / "src" / "ping.py").write_text("def ping():\n    return 1\n", encoding="utf-8")
    branch = workspace.publish()
    show = _git(repo, "show", f"{branch}:.env", check=False)
    assert show.returncode != 0
    assert SECRET not in _git(repo, "grep", "-I", "-n", SECRET, branch, check=False).stdout

    (repo / ".env").unlink()
    fresh = SprintWorkspace(str(repo), "new-env", 2)
    fresh_root = Path(fresh.create())
    (fresh_root / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError):
        fresh.publish()
    assert SECRET not in _git(repo, "grep", "-I", "-n", SECRET, "HEAD", check=False).stdout


def test_tracked_secret_file_is_not_deleted(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
    _commit_all(repo)
    workspace = SprintWorkspace(str(repo), "keep-tracked-env", 1)
    root = Path(workspace.create())
    (root / "ping.py").write_text("x = 1\n", encoding="utf-8")
    branch = workspace.publish()
    assert _git(repo, "cat-file", "-e", f"{branch}:.env").returncode == 0
    assert "D\t.env" not in _git(repo, "diff", "--name-status", "HEAD", branch).stdout


def test_configured_filter_is_refused_before_it_runs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "filter-marker"
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=leak\n", encoding="utf-8")
    _commit_all(repo)
    _git(repo, "config", "--local", "filter.leak.clean", f"touch {marker}")
    _git(repo, "config", "--local", "filter.leak.smudge", f"touch {marker}")
    workspace = SprintWorkspace(str(repo), "no-filter", 1)
    root = Path(workspace.create())
    (root / "ping.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError, match="filter"):
        workspace.publish()
    assert not marker.exists()


def test_no_head_with_filter_does_not_touch_the_repository(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "filter-marker"
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=leak\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "--local", "filter.leak.clean", f"touch {marker}")
    _git(repo, "config", "--local", "filter.leak.smudge", f"touch {marker}")
    config_before = (repo / ".git" / "config").read_bytes()
    index_path = repo / ".git" / "index"
    index_before = index_path.read_bytes() if index_path.exists() else None
    readme_before = (repo / "README.md").read_bytes()
    workspace = SprintWorkspace(str(repo), "no-head-filter", 1)
    root = Path(workspace.create())
    (root / "ping.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError, match="no commit on HEAD"):
        workspace.publish()
    assert not marker.exists()
    assert (repo / ".git" / "config").read_bytes() == config_before
    assert (repo / "README.md").read_bytes() == readme_before
    index_after = index_path.read_bytes() if index_path.exists() else None
    assert index_after == index_before
    refs = _git(repo, "show-ref", check=False)
    assert refs.stdout.strip() == ""
    assert "agentforge/" not in refs.stdout


def test_plain_directory_is_not_initialized(tmp_path: Path):
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    before = (repo / "README.md").read_bytes()
    workspace = SprintWorkspace(str(repo), "plain", 1)
    root = Path(workspace.create())
    (root / "ping.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(PublishRejectedError, match="not a Git repository"):
        workspace.publish()
    assert not (repo / ".git").exists()
    assert (repo / "README.md").read_bytes() == before


def test_deadline_after_commit_keeps_the_branch_name(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all(repo)
    workspace = SprintWorkspace(str(repo), "residual", 1)
    root = Path(workspace.create())
    (root / "ping.py").write_text("x = 1\n", encoding="utf-8")
    hot = {"on": False}
    import src.harness.workspace as workspace_module

    real_git = workspace_module._git

    def wrapped(cwd: Path, *args: str, check: bool = True):
        result = real_git(cwd, *args, check=check)
        if args and args[0] == "commit" and "apply task" in " ".join(args):
            hot["on"] = True
        return result

    monkeypatch.setattr(workspace_module, "_git", wrapped)
    monkeypatch.setattr(workspace_module, "deadline_exceeded", lambda: hot["on"])
    with pytest.raises(DeadlineExceededError) as caught:
        workspace.publish()
    assert caught.value.branch
    assert "not an accepted result" not in str(caught.value) or caught.value.branch
    listed = _git(repo, "branch", "--list", caught.value.branch)
    assert caught.value.branch.split("/")[-1] in listed.stdout or caught.value.branch in listed.stdout


def test_delivery_diff_is_redacted_without_being_truncated():
    payload = ("a" * 600) + KEY
    assert "(truncated)" not in sanitize_delivery(payload)
    assert len(sanitize_delivery(payload)) > 500
    assert KEY not in sanitize_delivery(payload)
    assert "(truncated)" in sanitize("e" * 600)

    manager = TaskManager()
    database = Path.cwd() / ".agentforge" / "redact-test.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    manager.bind(str(database))
    try:
        task = manager.create_task("diff", ".")
        manager.set_result(task.task_id, {"code_diff": payload, "sprint_contract": [{"text": payload}]})
        saved = manager.get_task(task.task_id)
        assert saved is not None
        assert len(saved.result["code_diff"]) > 500
        assert KEY not in saved.result["code_diff"]
        assert "(truncated)" not in saved.result["code_diff"]
        json.loads(json.dumps(saved.result))
        assert saved.status == TaskStatus.COMPLETED
    finally:
        manager.use_memory()
        database.unlink(missing_ok=True)


def test_terminal_status_cannot_be_reopened(tmp_path: Path):
    manager = TaskManager()
    manager.bind(str(tmp_path / "tasks.sqlite"))
    task = manager.create_task("once", str(tmp_path))
    manager.set_error(task.task_id, "stopped")
    manager.update_status(task.task_id, TaskStatus.RUNNING)
    manager.set_result(task.task_id, {"final_verdict": "PASS"})
    saved = manager.get_task(task.task_id)
    assert saved is not None
    assert saved.status == TaskStatus.FAILED
    assert saved.result is None or saved.result.get("final_verdict") != "PASS"
    manager.use_memory()


def test_compose_mounts_the_task_database():
    import shutil

    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "agentforge-tasks:/var/lib/agentforge" in compose
    assert "TASK_DB_PATH: /var/lib/agentforge/tasks.sqlite" in compose
    assert "TASK_DB_PATH=/var/lib/agentforge/tasks.sqlite" in dockerfile
    commands = []
    if shutil.which("docker"):
        commands.append(["docker", "compose", "config"])
    if shutil.which("docker-compose"):
        commands.append(["docker-compose", "config"])
    rendered = None
    for command in commands:
        completed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            rendered = completed.stdout
            break
    if rendered is None:
        pytest.skip("docker compose is not available to render the file")
    assert "/var/lib/agentforge" in rendered
    assert "agentforge-tasks" in rendered
