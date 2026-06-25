from __future__ import annotations

import json
import os
import tempfile
import time

from langchain_core.messages import ToolMessage

from src.agents.base import BaseAgent
from src.agents.prompts.generator import (
    GENERATOR_RETRY_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    GENERATOR_USER_PROMPT,
)
from src.harness.loop.controller import LoopController
from src.harness.safety.hooks import pre_write_hook
from src.tools.file_ops import read_file
from src.tools.registry import registry
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
        contract = state.get("sprint_contract", [])
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

        while self.loop_controller.can_continue(iteration, self.llm.total_tokens_used):
            iteration += 1

            # Call LLM with tools
            tools = registry.to_langchain_tools()
            response = await self.llm.chat(messages, tools=tools)

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
                tool_args = tc["args"]
                result_str = ""

                # Check for repeated actions
                action_hash = LoopController.hash_action(tool_name, tool_args)
                if self.loop_controller.should_force_strategy_change(action_hash):
                    result_str = "BLOCKED: Action repeated too many times. Try a different approach."
                    tool_calls_log.append({
                        "tool_name": tool_name,
                        "args": tool_args,
                        "result": result_str,
                        "timestamp": time.time(),
                    })
                elif tool_name == "write_file":
                    # Safety check for write_file
                    hook_result = pre_write_hook(
                        tool_args.get("path", ""),
                        tool_args.get("content", ""),
                        sprint_workspace,
                    )
                    if hook_result.exit_code != 0:
                        result_str = f"BLOCKED: {hook_result.reason}"
                        tool_calls_log.append({
                            "tool_name": tool_name,
                            "args": tool_args,
                            "result": result_str,
                            "timestamp": time.time(),
                        })
                    else:
                        self.loop_controller.record_action(action_hash)
                        result_str = await self._execute_tool(tool_name, tool_args)
                        tool_calls_log.append({
                            "tool_name": tool_name,
                            "args": tool_args,
                            "result": result_str,
                            "timestamp": time.time(),
                        })
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
            "code_diff": code_diff,
            "execution_trace": tool_calls_log,
        }

    def _create_sprint_workspace(self, state: AgentState) -> str:
        """Create a temporary directory for this sprint's work.

        Note: Cleanup should be handled by the caller after sprint completes.
        """
        task_id = state.get("task_id", "default")
        sprint = state.get("current_sprint", 1)
        workspace = os.path.join(tempfile.gettempdir(), f"agentforge_sprint_{task_id}_{sprint}")
        os.makedirs(workspace, exist_ok=True)
        return workspace

    @staticmethod
    def cleanup_sprint_workspace(workspace: str) -> None:
        """Clean up sprint workspace after completion.

        Args:
            workspace: Path to the sprint workspace directory
        """
        import shutil
        try:
            if os.path.exists(workspace) and "agentforge_sprint_" in workspace:
                shutil.rmtree(workspace)
        except Exception:
            pass  # Best effort cleanup

    async def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return the result string."""
        try:
            result = await registry.execute(tool_name, **tool_args)
            return str(result)[:self.MAX_RESULT_CHARS]
        except Exception as e:
            return f"Error: {e}"

    def _compute_diff(self, sprint_workspace: str, codebase_path: str) -> str:
        """Compute a summary of changes made in the sprint workspace."""
        diff_parts = []
        for root, dirs, files in os.walk(sprint_workspace):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    rel_path = os.path.relpath(fpath, sprint_workspace)
                    diff_parts.append(f"--- /dev/null\n+++ b/{rel_path}\n{content}")
                except (UnicodeDecodeError, PermissionError):
                    continue
        return "\n".join(diff_parts) if diff_parts else "(no files written)"
