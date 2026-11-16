from __future__ import annotations

import difflib
import json
import os
import time
from pathlib import Path

from langchain_core.messages import ToolMessage

from src.agents.base import BaseAgent
from src.agents.prompts.generator import (
    GENERATOR_RETRY_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    GENERATOR_USER_PROMPT,
)
from src.harness.loop.controller import LoopController
from src.harness.safety.hooks import guard_tool_call
from src.harness.workspace import SprintWorkspace
from src.tools.file_ops import read_file
from src.tools.registry import registry
from src.workflow.sprints import active_criteria
from src.workflow.state import AgentState


class GeneratorAgent(BaseAgent):
    """Generator: implements code using tools in a loop."""

    MAX_RESULT_CHARS: int = 2000  # Max characters kept from a tool result
    MAX_SUMMARY_CHARS: int = 500  # Max characters kept from final response summary

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.loop_controller = LoopController()

    async def execute(self, state: AgentState) -> AgentState:
        """Implement code to satisfy the sprint contract."""
        sprint_workspace = self._create_sprint_workspace(state)
        contract = active_criteria(state)
        plan = state.get("plan", [])
        current_sprint = state.get("current_sprint", 1)
        eval_feedback = state.get("eval_feedback")
        codebase_path = state.get("codebase_path", "./toy-repo")

        # Get sprint goal from plan
        sprint_goal = "Implement the requested changes"
        for step in plan:
            if step.get("id") == current_sprint:
                sprint_goal = step.get("description", sprint_goal)
                break

        # Read conventions
        conventions_path = f"{codebase_path}/CONVENTIONS.md"
        try:
            conventions = read_file(conventions_path)
        except FileNotFoundError:
            conventions = "No conventions found."

        # Build prompt
        contract_text = json.dumps(contract, indent=2)
        eval_feedback_section = ""
        if eval_feedback:
            eval_feedback_section = GENERATOR_RETRY_PROMPT.format(eval_feedback=eval_feedback)

        system = GENERATOR_SYSTEM_PROMPT.format(
            sprint_workspace=sprint_workspace,
            sprint_contract=contract_text,
            conventions=self._truncate(conventions),
            eval_feedback_section=eval_feedback_section,
        )
        user = GENERATOR_USER_PROMPT.format(
            sprint_number=current_sprint,
            sprint_goal=sprint_goal,
            codebase_path=codebase_path,
        )

        # Tool-calling loop
        messages = self._build_messages(system, user)
        tool_calls_log: list[dict] = []
        iteration = 0

        # Reset token baseline so the budget measures THIS loop's consumption,
        # not the cumulative total across all agents sharing the same LLMClient.
        self.loop_controller.reset_run(self.llm.total_tokens_used)

        while self.loop_controller.can_continue(iteration, self.llm.total_tokens_used):
            iteration += 1

            # Call LLM with tools
            tools = registry.to_langchain_tools()
            response = await self.record_cost(messages, state, role="generator", tools=tools)

            # If no tool calls, the LLM is done
            if not response.tool_calls:
                # Extract final summary from the response
                summary = response.content if isinstance(response.content, str) else str(response.content)
                tool_calls_log.append({
                    "tool_name": "final_response",
                    "args": {},
                    "result": summary[:self.MAX_SUMMARY_CHARS],
                    "timestamp": time.time(),
                })
                break

            # Execute each tool call
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = dict(tc["args"])  # copy to avoid mutating the original
                result_str = ""

                # Resolve relative paths against sprint workspace so that
                # pre_write_hook sees the correct absolute path and tools
                # read/write in the right directory.
                self._resolve_tool_paths(tool_args, sprint_workspace)

                # Check for repeated actions, then confine every path-bearing tool
                # to the codebase or the sprint workspace.
                action_hash = LoopController.hash_action(tool_name, tool_args)
                if self.loop_controller.should_force_strategy_change(action_hash):
                    result_str = "BLOCKED: Action repeated too many times. Try a different approach."
                else:
                    hook_result = guard_tool_call(
                        tool_name,
                        tool_args,
                        sprint_workspace=sprint_workspace,
                        codebase_path=codebase_path,
                    )
                    if hook_result.exit_code != 0:
                        result_str = f"BLOCKED: {hook_result.reason}"
                    else:
                        self.loop_controller.record_action(action_hash)
                        result_str = await self._execute_tool(tool_name, tool_args)
                tool_calls_log.append({
                    "tool_name": tool_name,
                    "args": tool_args,
                    "result": result_str,
                    "timestamp": time.time(),
                })

                # Always respond with ToolMessage for each tool_call_id
                messages.append(ToolMessage(
                    content=result_str,
                    tool_call_id=tc["id"],
                ))

        # Compute code_diff from sprint workspace
        code_diff = self._compute_diff(sprint_workspace, codebase_path)

        return {
            **state,
            "sprint_workspace": sprint_workspace,
            "code_diff": code_diff,
            "execution_trace": tool_calls_log,
            "error": None,
        }

    def _create_sprint_workspace(self, state: AgentState) -> str:
        """Create a seeded sprint workspace by copying existing codebase.

        On the first attempt (retry_count == 0), always reseed from the
        codebase.  On retries, preserve the previous attempt's code so the
        Generator can do targeted fixes instead of rewriting from scratch.
        """
        codebase_path = state.get("codebase_path", "./toy-repo")
        task_id = state.get("task_id", "default")
        sprint = state.get("current_sprint", 1)
        retry_count = state.get("retry_count", 0)
        existing = state.get("sprint_workspace") or ""
        workspace = SprintWorkspace(codebase_path, task_id, sprint)
        if state.get("preserve_workspace") and existing:
            workspace.path = Path(existing)
            return workspace.create(force_reseed=False)
        return workspace.create(force_reseed=(retry_count == 0))

    @staticmethod
    def _resolve_tool_paths(tool_args: dict, sprint_workspace: str) -> None:
        """Resolve relative paths in tool args against the sprint workspace.

        LLMs typically return workspace-relative paths (e.g. ``src/models/tag.py``).
        Without resolution, ``pre_write_hook`` would compare the CWD-relative
        resolve against the sprint workspace and reject the write.
        """
        if "path" not in tool_args:
            return
        p = Path(tool_args["path"])
        if not p.is_absolute():
            tool_args["path"] = str(Path(sprint_workspace) / p)

    async def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return the result string."""
        try:
            result = await registry.execute(tool_name, **tool_args)
            return str(result)[:self.MAX_RESULT_CHARS]
        except Exception as e:
            return f"Error: {e}"

    def _compute_diff(self, sprint_workspace: str, codebase_path: str) -> str:
        """Compute a unified diff between sprint_workspace and codebase_path.

        Includes modifications, additions, and deletions. New files use
        ``--- /dev/null`` and removed files use ``+++ /dev/null``.
        """
        diff_parts: list[str] = []
        workspace_files = _iter_text_files(sprint_workspace)
        codebase_files = _iter_text_files(codebase_path) if os.path.isdir(codebase_path) else {}

        for rel_path, new_content in workspace_files.items():
            old_content = codebase_files.get(rel_path)
            if old_content is None:
                diff_lines = difflib.unified_diff(
                    [],
                    new_content.splitlines(keepends=True),
                    fromfile="/dev/null",
                    tofile=f"b/{rel_path}",
                )
            elif old_content == new_content:
                continue
            else:
                diff_lines = difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
            diff_str = "".join(diff_lines)
            if diff_str:
                diff_parts.append(diff_str)

        for rel_path, old_content in codebase_files.items():
            if rel_path in workspace_files:
                continue
            diff_lines = difflib.unified_diff(
                old_content.splitlines(keepends=True),
                [],
                fromfile=f"a/{rel_path}",
                tofile="/dev/null",
            )
            diff_str = "".join(diff_lines)
            if diff_str:
                diff_parts.append(diff_str)

        return "\n".join(diff_parts) if diff_parts else "(no changes)"


_DIFF_SKIP_DIRS = frozenset({
    "__pycache__", ".git", "node_modules", ".pytest_cache", ".venv", "venv", "dist", "build",
})


def _iter_text_files(root: str) -> dict[str, str]:
    """Map relative path to UTF-8 text for files under root."""
    found: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _DIFF_SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, encoding="utf-8") as handle:
                    found[os.path.relpath(full, root)] = handle.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
    return found
