import json
import os
import time
from typing import Any, Dict, Generator, List, Mapping, Optional, Sequence, Tuple, Union

from ollama import Client
from ollama._types import Message

from backend import state
from backend.agents.agent_run import (
    finish_run,
    get_active_run,
    start_run,
    update_run,
)
from backend.agents.task_context import (
    find_task_by_id,
    record_task_transcript,
    sync_task_files_from_transcript,
)
from backend.agents.tools import ToolRegistry
from backend.services.logs import add_system_log
from backend.services.tool_execution_service import execute_tool
from backend.services.workflow_settings import get_workflow_settings
from backend.storage.memory_engine import SemanticMemoryEngine

ChatMessage = Union[Mapping[str, Any], Message]

SAME_ARGS_FAILURE_LIMIT = 3


def _normalize_tool_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


class ScrumAgent:
    def __init__(
        self,
        role: str,
        model: str,
        system_prompt: str,
        ollama_url: str = "http://localhost:11434",
    ):
        self.role = role
        self.model = model
        self.system_prompt = system_prompt
        self.ollama_url = ollama_url.rstrip("/")
        self.memory = SemanticMemoryEngine(ollama_url=self.ollama_url)
        self.registry = ToolRegistry()
        self.assigned_skills: List[str] = []
        self._client: Optional[Client] = None
        self._client_host: Optional[str] = None

    def register_tool(self, tool) -> None:
        self.registry.register(tool)

    def _get_client(self) -> Client:
        if self._client is None or self._client_host != self.ollama_url:
            self._client = Client(host=self.ollama_url, timeout=120.0)
            self._client_host = self.ollama_url
        return self._client

    def _get_skills_context(self) -> str:
        if not self.assigned_skills:
            return ""

        skills_context = "\n=== SPECIALIZED AGENT SKILLS ===\n"
        for skill_file in self.assigned_skills:
            skill_path = os.path.join(state.SKILLS_DIR, skill_file)
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        skills_context += f"\n[Skill: {skill_file}]\n{f.read()}\n"
                except Exception:
                    pass
        return skills_context

    def _build_system_content(self) -> str:
        return self.system_prompt + self._get_skills_context()

    def _build_user_content(self, user_prompt: str) -> str:
        related_memories = self.memory.search(self.role, user_prompt, limit=2)
        memory_context = ""
        if related_memories:
            memory_context = "\n=== RELEVANT HISTORICAL MEMORIES ===\n" + "\n".join(
                [f"[{m['category']}] {m['content']}" for m in related_memories]
            )
        parts = [part for part in (memory_context, f"Task Detail:\n{user_prompt}") if part]
        return "\n\n".join(parts)

    def _chat_options(self) -> Dict[str, Any]:
        return {"temperature": 0.1}

    def _chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        stream: bool = False,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ):
        client = self._get_client()
        delays = [0, 1, 2, 4]

        for delay in delays:
            if delay:
                time.sleep(delay)
            try:
                return client.chat(
                    model=self.model,
                    messages=list(messages),
                    tools=tools,
                    stream=stream,
                    options=self._chat_options(),
                )
            except Exception:
                continue

        return None

    def _log_step_exit(self, message: str, log_type: str = "warning") -> None:
        add_system_log(self.role, log_type, message)

    def _process_tool_calls(
        self,
        message: Message,
        messages: List[ChatMessage],
        user_prompt: str,
        failed_tool_keys: List[Tuple[str, str]],
        total_failures: List[int],
        max_tool_failures: int,
    ) -> Optional[str]:
        """Process tool calls; return early-stop message when limits exceeded."""
        messages.append(message)
        run = get_active_run()
        task_id = state.ACTIVE_SPRINT_TASK_ID
        from backend.agents.registry import AGENT_MAP

        agent_id = next((aid for aid, a in AGENT_MAP.items() if a is self), "dev")

        for call in message.tool_calls or []:
            tool_name = call.function.name
            arguments = _normalize_tool_arguments(call.function.arguments)

            run_id = run.run_id if run else "NO-RUN"
            result = execute_tool(
                agent_id,
                tool_name,
                arguments,
                task_id=task_id,
                source="agent",
                skip_approval=False,
                run_id=run_id,
                user_prompt=user_prompt,
                on_awaiting_approval=lambda name: update_run(status="awaiting_approval", current_tool=name),
                on_tool_executing=lambda name: update_run(status="tool_executing", current_tool=name),
            )
            tool_output = result.tool_output
            success = result.success
            update_run(status="thinking", clear_tool=True)

            if not success:
                total_failures[0] += 1
                key = (tool_name, json.dumps(arguments, sort_keys=True))
                failed_tool_keys.append(key)
                if failed_tool_keys.count(key) >= SAME_ARGS_FAILURE_LIMIT:
                    stop_msg = (
                        f"Stopped: tool '{tool_name}' failed repeatedly with the same arguments. "
                        f"Last error: {tool_output[:200]}"
                    )
                    self._log_step_exit(stop_msg, "error")
                    finish_run(status="failed", error=stop_msg)
                    return stop_msg
                if total_failures[0] >= max_tool_failures:
                    stop_msg = (
                        f"Stopped: {total_failures[0]} tool failures this step (limit {max_tool_failures}). "
                        f"Last error ({tool_name}): {tool_output[:200]}"
                    )
                    self._log_step_exit(stop_msg, "error")
                    finish_run(status="failed", error=stop_msg)
                    return stop_msg

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": tool_output,
                }
            )
            if not success:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Tool '{tool_name}' failed: {tool_output[:300]}. "
                            "Do not repeat the same arguments. Try a different path, "
                            "command, or approach to achieve the task."
                        ),
                    }
                )
        return None

    def execute_step(self, user_prompt: str, max_iterations: int = 8) -> str:
        tools = self.registry.get_ollama_tools()
        messages: List[ChatMessage] = [
            {"role": "system", "content": self._build_system_content()},
            {"role": "user", "content": self._build_user_content(user_prompt)},
        ]

        failed_tool_keys: List[Tuple[str, str]] = []
        total_failures: List[int] = [0]
        ws = get_workflow_settings()
        max_tool_failures = int(ws.get("maxToolFailuresPerStep", 5))
        task_id = state.ACTIVE_SPRINT_TASK_ID
        if task_id:
            start_run(task_id, self.role, max_iterations=max_iterations)

        try:
            for iteration in range(1, max_iterations + 1):
                update_run(
                    status="thinking",
                    iteration=iteration,
                    max_iterations=max_iterations,
                )
                add_system_log(
                    self.role,
                    "info",
                    f"LLM iteration {iteration}/{max_iterations}",
                )
                response = self._chat(messages, tools=tools or None)
                if response is None:
                    self._log_step_exit("Ollama unavailable — SIMULATION_FALLBACK", "warning")
                    finish_run(status="failed", error="SIMULATION_FALLBACK")
                    return "SIMULATION_FALLBACK"

                message = response.message
                if message.tool_calls:
                    early_stop = self._process_tool_calls(
                        message,
                        messages,
                        user_prompt,
                        failed_tool_keys,
                        total_failures,
                        max_tool_failures,
                    )
                    if early_stop:
                        return early_stop
                    continue

                content = (message.content or "").strip()
                if task_id and content:
                    record_task_transcript(
                        task_id,
                        "assistant",
                        content,
                        agent=self.role,
                    )
                finish_run(status="completed")
                return content or "Task completed."

            max_msg = "Max tool iterations reached without completing the task."
            self._log_step_exit(max_msg, "warning")
            finish_run(status="failed", error=max_msg)
            return max_msg
        except Exception as exc:
            finish_run(status="failed", error=str(exc))
            raise
        finally:
            if task_id:
                task = find_task_by_id(task_id)
                if task:
                    sync_task_files_from_transcript(task)
                    from backend.services.board_service import publish_board_update
                    from backend.services.project_service import save_current_project_state

                    save_current_project_state()
                    publish_board_update(task_id, source="task_files")


    def stream_messages(
        self,
        messages: List[Dict[str, str]],
    ) -> Generator[str, None, None]:
        """Streams Ollama chat response chunks; yields fallback token on failure."""
        full_messages: List[ChatMessage] = [
            {"role": "system", "content": self._build_system_content()},
            *messages,
        ]

        stream = self._chat(full_messages, stream=True)
        if stream is None:
            yield "SIMULATION_FALLBACK"
            return

        try:
            for chunk in stream:
                content = chunk.message.content
                if content:
                    yield content
        except Exception:
            yield "SIMULATION_FALLBACK"
