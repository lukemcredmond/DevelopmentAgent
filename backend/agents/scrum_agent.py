import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from backend.agents.tool_outcomes import parse_run_command_exit
from backend.services.diagnostics_parser import parse_command_diagnostics
from backend.services.parallel_tools import partition_tool_calls
from backend.services.tool_execution_service import ToolExecutionResult, execute_tool
from backend.services.workflow_settings import get_workflow_settings
from backend.storage.memory_engine import create_memory_engine

ChatMessage = Union[Mapping[str, Any], Message]

SAME_ARGS_FAILURE_LIMIT = 3
_FAILURE_LOCK = threading.Lock()


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
        from backend.storage.memory_engine import create_memory_engine

        self.memory = create_memory_engine(ollama_url=self.ollama_url)
        self.registry = ToolRegistry()
        self.assigned_skills: List[str] = []
        self._client: Optional[Client] = None
        self._client_host: Optional[str] = None
        self._last_memories_used: List[Dict[str, Any]] = []
        self._decisions_in_prompt: int = 0

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

        from backend.services.prompt_budget import skills_context_max_chars
        from backend.services.workflow_settings import get_workflow_settings

        max_chars = skills_context_max_chars(int(get_workflow_settings().get("ollamaNumCtx", 32768)))
        skills_context = "\n=== SPECIALIZED AGENT SKILLS ===\n"
        used = len(skills_context)
        truncated = False
        for skill_file in self.assigned_skills:
            skill_path = os.path.join(state.SKILLS_DIR, skill_file)
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        block = f"\n[Skill: {skill_file}]\n{f.read()}\n"
                    if used + len(block) > max_chars:
                        remaining = max_chars - used
                        if remaining > 100:
                            skills_context += block[: remaining - 30] + "\n...[skill truncated]\n"
                        truncated = True
                        break
                    skills_context += block
                    used += len(block)
                except Exception:
                    pass
        if truncated:
            skills_context += "\n[Additional skills omitted — context budget exceeded]\n"
        return skills_context

    def _build_system_content(self) -> str:
        return self.system_prompt + self._get_skills_context()

    def _build_user_content(self, user_prompt: str) -> str:
        from backend import state

        project_id = state.CURRENT_PROJECT_ID or "default-proj"
        related_memories = self.memory.search(
            self.role,
            user_prompt,
            limit=3,
            project_id=project_id,
        )
        self._last_memories_used = related_memories
        memory_context = ""
        if related_memories:
            memory_context = "\n=== RELEVANT HISTORICAL MEMORIES ===\n" + "\n".join(
                [f"[{m['category']}] {m['content']}" for m in related_memories]
            )
        parts = [part for part in (memory_context, f"Task Detail:\n{user_prompt}") if part]
        return "\n\n".join(parts)

    def _chat_options(self) -> Dict[str, Any]:
        ws = get_workflow_settings()
        opts: Dict[str, Any] = {
            "temperature": 0.1,
            "num_ctx": int(ws.get("ollamaNumCtx", 32768)),
        }
        keep_alive = ws.get("ollamaKeepAlive")
        if keep_alive:
            opts["keep_alive"] = str(keep_alive)
        return opts

    @staticmethod
    def _is_context_overflow_error(error: str) -> bool:
        lower = error.lower()
        return "exceed_context" in lower or "context size" in lower

    def _context_overflow_message(self) -> str:
        ws = get_workflow_settings()
        num_ctx = int(ws.get("ollamaNumCtx", 32768))
        return (
            f"Request exceeded Ollama context (num_ctx={num_ctx}). "
            "Increase Ollama context size in Workflow settings, or shorten the project brief / remove assigned skills."
        )

    def _chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        stream: bool = False,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        iteration: int = 0,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        from backend.agents.registry import AGENT_MAP
        from backend.services.llm_debug_log import append_llm_log_entry

        client = self._get_client()
        delays = [0, 1, 2, 4]
        if agent_id is None:
            agent_id = next((aid for aid, a in AGENT_MAP.items() if a is self), "dev")
        tid = task_id or state.ACTIVE_SPRINT_TASK_ID
        active_run = get_active_run()
        run_id = active_run.run_id if active_run else None
        tool_names = [t.get("function", {}).get("name") for t in (tools or []) if isinstance(t, dict)]

        for delay in delays:
            if delay:
                time.sleep(delay)
            started = time.time()
            last_error: Optional[str] = None
            try:
                result = client.chat(
                    model=self.model,
                    messages=list(messages),
                    tools=tools,
                    stream=stream,
                    options=self._chat_options(),
                )
                duration_ms = int((time.time() - started) * 1000)
                if not stream and result is not None:
                    msg = result.message
                    tool_calls = []
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_calls.append(
                                {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                }
                            )
                    append_llm_log_entry(
                        agent=self.role,
                        agent_id=agent_id or "dev",
                        task_id=tid,
                        run_id=run_id,
                        model=self.model,
                        iteration=iteration,
                        request_messages=messages,
                        tool_names=[n for n in tool_names if n],
                        response_content=(msg.content or "") if msg else "",
                        response_tool_calls=tool_calls,
                        duration_ms=duration_ms,
                        memories_used=getattr(self, "_last_memories_used", None),
                        decisions_included=getattr(self, "_decisions_in_prompt", None),
                    )
                return result
            except Exception as exc:
                last_error = str(exc)
                duration_ms = int((time.time() - started) * 1000)
                append_llm_log_entry(
                    agent=self.role,
                    agent_id=agent_id or "dev",
                    task_id=tid,
                    run_id=run_id,
                    model=self.model,
                    iteration=iteration,
                    request_messages=messages,
                    tool_names=[n for n in tool_names if n],
                    duration_ms=duration_ms,
                    error=last_error,
                    memories_used=getattr(self, "_last_memories_used", None),
                    decisions_included=getattr(self, "_decisions_in_prompt", None),
                )
                if self._is_context_overflow_error(last_error):
                    overflow_msg = self._context_overflow_message()
                    add_system_log(self.role, "error", overflow_msg)
                    return None
                continue

        return None

    def _log_step_exit(self, message: str, log_type: str = "warning") -> None:
        add_system_log(self.role, log_type, message)

    def _execute_single_tool_call(
        self,
        call: Any,
        *,
        task_id: Optional[str],
        agent_id: str,
        run_id: str,
        user_prompt: str,
        failed_tool_keys: List[Tuple[str, str]],
        total_failures: List[int],
        max_tool_failures: int,
    ) -> Tuple[str, Dict[str, Any], ToolExecutionResult, Optional[str]]:
        """Returns (tool_name, arguments, result, early_stop_message)."""
        tool_name = call.function.name
        arguments = _normalize_tool_arguments(call.function.arguments)
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
        update_run(status="thinking", clear_tool=True)

        if result.pending_approval:
            stop_msg = result.tool_output
            finish_run(status="awaiting_approval", error=stop_msg)
            return tool_name, arguments, result, stop_msg

        if not result.success and not result.pending_approval:
            with _FAILURE_LOCK:
                total_failures[0] += 1
                key = (tool_name, json.dumps(arguments, sort_keys=True))
                failed_tool_keys.append(key)
                same_count = failed_tool_keys.count(key)
                fail_total = total_failures[0]
            if same_count >= SAME_ARGS_FAILURE_LIMIT:
                stop_msg = (
                    f"Stopped: tool '{tool_name}' failed repeatedly with the same arguments. "
                    f"Last error: {result.tool_output[:200]}"
                )
                self._log_step_exit(stop_msg, "error")
                finish_run(status="failed", error=stop_msg)
                return tool_name, arguments, result, stop_msg
            if fail_total >= max_tool_failures:
                stop_msg = (
                    f"Stopped: {total_failures[0]} tool failures this step (limit {max_tool_failures}). "
                    f"Last error ({tool_name}): {result.tool_output[:200]}"
                )
                self._log_step_exit(stop_msg, "error")
                finish_run(status="failed", error=stop_msg)
                return tool_name, arguments, result, stop_msg
        return tool_name, arguments, result, None

    def _append_tool_messages(
        self,
        messages: List[ChatMessage],
        tool_name: str,
        arguments: Dict[str, Any],
        tool_output: str,
        success: bool,
    ) -> None:
        from backend.services.llm_context import truncate_tool_output_for_llm

        llm_output = truncate_tool_output_for_llm(tool_name, tool_output)
        messages.append(
            {
                "role": "tool",
                "tool_name": tool_name,
                "content": llm_output,
            }
        )
        if not success:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Tool '{tool_name}' failed: {llm_output[:300]}. "
                        "Do not repeat the same arguments. Try a different path, "
                        "command, or approach to achieve the task."
                        + (
                            " apply_patch failed — call read_file on the same path, "
                            "then retry with exact old_text from that result. Never use "
                            "analyze output or pre-loaded context."
                            if tool_name == "apply_patch"
                            else ""
                        )
                    ),
                }
            )
        elif tool_name == "run_command":
            exit_code, body = parse_run_command_exit(llm_output)
            command = str(arguments.get("command") or "")
            diagnostics = parse_command_diagnostics(command, body or llm_output)
            if diagnostics:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Command returned {len(diagnostics)} problems — fix each "
                            "file:line listed above before re-running."
                        ),
                    }
                )
            elif exit_code is not None and exit_code > 0:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Command completed with findings (non-zero exit). "
                            "Fix listed issues with apply_patch/write_file, then re-run "
                            "the lint command once. Do not repeat the same command without making changes."
                        ),
                    }
                )

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
        run_id = run.run_id if run else "NO-RUN"
        all_calls = list(message.tool_calls or [])
        parallel_calls, sequential_calls = partition_tool_calls(all_calls)
        results_by_id: Dict[int, Tuple[str, Dict[str, Any], ToolExecutionResult, Optional[str]]] = {}

        if parallel_calls:
            with ThreadPoolExecutor(max_workers=min(8, len(parallel_calls))) as pool:
                future_map = {
                    pool.submit(
                        self._execute_single_tool_call,
                        call,
                        task_id=task_id,
                        agent_id=agent_id,
                        run_id=run_id,
                        user_prompt=user_prompt,
                        failed_tool_keys=failed_tool_keys,
                        total_failures=total_failures,
                        max_tool_failures=max_tool_failures,
                    ): call
                    for call in parallel_calls
                }
                for future, call in future_map.items():
                    results_by_id[id(call)] = future.result()

        for call in sequential_calls:
            results_by_id[id(call)] = self._execute_single_tool_call(
                call,
                task_id=task_id,
                agent_id=agent_id,
                run_id=run_id,
                user_prompt=user_prompt,
                failed_tool_keys=failed_tool_keys,
                total_failures=total_failures,
                max_tool_failures=max_tool_failures,
            )

        for call in all_calls:
            tool_name, arguments, result, early_stop = results_by_id[id(call)]
            if early_stop:
                return early_stop
            self._append_tool_messages(messages, tool_name, arguments, result.tool_output, result.success)
        return None

    def execute_step(self, user_prompt: str, max_iterations: int = 8) -> str:
        from backend.agents.registry import configure_agent_tools

        configure_agent_tools()
        from backend.storage.memory_engine import resolve_embed_model

        self.memory.embed_model = resolve_embed_model()
        tools = self.registry.get_ollama_tools()
        if not tools:
            add_system_log(
                self.role,
                "error",
                "No tools registered for this agent — check Workflow settings and restart the backend.",
            )
        self._last_memories_used = []
        self._decisions_in_prompt = 0
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
            active_task = find_task_by_id(task_id)
            if active_task:
                self._decisions_in_prompt = min(len(active_task.get("decisions") or []), 8)
            start_run(task_id, self.role, max_iterations=max_iterations)

        try:
            for iteration in range(1, max_iterations + 1):
                update_run(
                    status="thinking",
                    iteration=iteration,
                    max_iterations=max_iterations,
                )
                if task_id and state.SPRINT_PROGRESS_MAX:
                    from backend.agents.task_context import find_task_by_id
                    from backend.services.sprint_service import publish_sprint_progress

                    active = find_task_by_id(task_id) or {}
                    publish_sprint_progress(
                        phase="sprint_step",
                        step=state.SPRINT_PROGRESS_STEP or iteration,
                        max_steps=state.SPRINT_PROGRESS_MAX,
                        agent=self.role,
                        task_id=task_id,
                        task_title=str(active.get("title") or task_id),
                        status=f"LLM iter {iteration}/{max_iterations}",
                    )
                add_system_log(
                    self.role,
                    "info",
                    f"LLM iteration {iteration}/{max_iterations}",
                )
                from backend.services.llm_context import prune_messages_if_needed

                prune_messages_if_needed(messages)
                response = self._chat(
                    messages,
                    tools=tools or None,
                    iteration=iteration,
                    task_id=task_id,
                )
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
