import json
import os
import re
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
    get_task_lane,
    is_task_done,
    normalize_task,
    record_task_transcript,
    sync_task_files_from_transcript,
)
from backend.agents.tools import ToolRegistry
from backend.services.logs import add_system_log
from backend.agents.tool_outcomes import parse_run_command_exit
from backend.services.diagnostics_parser import parse_command_diagnostics
from backend.services.parallel_tools import partition_tool_calls
from backend.services.llm_tool_recovery import (
    apply_tool_call_recovery,
    assistant_message_to_chat_dict,
    normalize_tool_arguments,
    unwrap_llm_text,
)
from backend.services.tool_execution_service import ToolExecutionResult, execute_tool
from backend.services.workflow_settings import get_workflow_settings
from backend.storage.memory_engine import create_memory_engine

ChatMessage = Union[Mapping[str, Any], Message]

SAME_ARGS_FAILURE_LIMIT = 3
SAME_ARGS_SUCCESS_LIMIT = 3  # early-stop after repeated identical successes
_FAILURE_LOCK = threading.Lock()


def _log_duplicate_skip(
    *,
    agent: str,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_output: str,
    task_id: Optional[str],
    run_id: Optional[str],
    success: bool = True,
) -> None:
    """Keep skipped/blocked duplicates visible in the tool log (and so in Flow)."""
    try:
        from backend.services.tool_execution_service import log_duplicate_skip_event

        log_duplicate_skip_event(
            agent=agent,
            tool_name=tool_name,
            arguments=arguments,
            tool_output=tool_output,
            task_id=task_id,
            run_id=run_id,
            success=success,
        )
    except Exception:
        pass


def _dev_step_needs_more_tools(tools_used: set[str], task_id: Optional[str]) -> bool:
    """True when a Developer sprint step should not exit on text-only LLM output."""
    if state.ACTIVE_SPRINT_AGENT != "Developer" or not task_id:
        return False
    if tools_used & {"write_file", "apply_patch"}:
        return False
    if get_task_lane(task_id) != "In Progress":
        return False
    return True


def _looks_like_plan_response(content: str) -> bool:
    lower = content.lower()
    if "following steps remain" in lower or "steps remain" in lower:
        return True
    if "to complete the task" in lower:
        return True
    if re.search(r"next\s+\d+\s+things", lower):
        return True
    if "next steps" in lower or "things to do" in lower:
        return True
    if "we need to" in lower or "remaining steps" in lower:
        return True
    if "here's what" in lower or "here is what" in lower:
        return True
    if "the following" in lower:
        return True
    return bool(re.search(r"(?m)^\s*\d+\.", content))


_PLAN_REJECTION_MESSAGE = (
    "Do not respond with a plan or numbered steps. You already read the file — "
    "call apply_patch or write_file now using the read_file output above."
)


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
        self._client_timeout: Optional[float] = None
        self._last_memories_used: List[Dict[str, Any]] = []
        self._decisions_in_prompt: int = 0
        self._last_chat_error_type: Optional[str] = None
        self._last_chat_error: Optional[str] = None

    def register_tool(self, tool) -> None:
        self.registry.register(tool)

    def _ollama_timeout_sec(self) -> float:
        return float(get_workflow_settings().get("ollamaRequestTimeoutSec", 300))

    def _ollama_max_retries(self) -> int:
        return max(1, int(get_workflow_settings().get("ollamaMaxRetries", 4)))

    def _ollama_retry_delays(self) -> List[int]:
        ws = get_workflow_settings()
        raw = ws.get("ollamaRetryDelaySec")
        if isinstance(raw, list) and raw:
            return [max(0, int(d)) for d in raw]
        return [0, 2, 5, 10]

    def _get_client(self) -> Client:
        timeout = self._ollama_timeout_sec()
        if (
            self._client is None
            or self._client_host != self.ollama_url
            or self._client_timeout != timeout
        ):
            self._client = Client(host=self.ollama_url, timeout=timeout)
            self._client_host = self.ollama_url
            self._client_timeout = timeout
        return self._client

    def _get_skills_context(self) -> str:
        if not self.assigned_skills:
            return ""

        from backend.services.prompt_budget import resolve_ollama_num_ctx, skills_context_max_chars

        skill_files = list(self.assigned_skills)
        task_id = state.ACTIVE_SPRINT_TASK_ID
        if task_id:
            task = find_task_by_id(task_id)
            if task:
                normalize_task(task)
                rec = [s for s in (task.get("recommendedSkillFiles") or []) if s]
                if rec:
                    allowed = set(self.assigned_skills)
                    skill_files = [s for s in rec if s in allowed]
                elif str(task.get("focusMode") or "whole") != "whole":
                    skill_files = list(self.assigned_skills)[:2]
                elif self.role in ("Code Reviewer", "QA Tester"):
                    skill_files = list(self.assigned_skills)

        if not skill_files:
            return ""

        max_chars = skills_context_max_chars(resolve_ollama_num_ctx(self.role))
        skills_context = "\n=== SPECIALIZED AGENT SKILLS ===\n"
        used = len(skills_context)
        truncated = False
        from backend.services.skills import resolve_skill_read_path

        for skill_file in skill_files:
            skill_path = resolve_skill_read_path(skill_file)
            if skill_path:
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
        query = user_prompt
        prefer = ["fix_pattern", "step_lesson"]
        task_id = state.ACTIVE_SPRINT_TASK_ID
        if task_id:
            task = find_task_by_id(task_id)
            if task:
                title = str(task.get("title") or "")
                outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
                stop = str(outcome.get("stopReason") or outcome.get("exitReason") or "")
                if title or stop:
                    query = f"{title}\n{stop}\n{user_prompt}"[:2000]
                if stop in (
                    "duplicate_tool",
                    "tool_failure_stop",
                    "step_timeout",
                    "plan_exhausted",
                    "max_iterations",
                ):
                    prefer = ["failure", "fix_pattern"]
        related_memories = self.memory.search(
            self.role,
            query,
            limit=3,
            project_id=project_id,
            include_all_agents=True,
            prefer_categories=prefer,
        )
        self._last_memories_used = related_memories
        memory_context = ""
        if related_memories:
            memory_context = "\n=== RELEVANT HISTORICAL MEMORIES ===\n" + "\n".join(
                [f"[{m['category']}] {m['content']}" for m in related_memories]
            )
        parts = [part for part in (memory_context, f"Task Detail:\n{user_prompt}") if part]
        return "\n\n".join(parts)

    def _save_step_lesson(
        self,
        stop_reason: str,
        tools_used: set[str],
        result_snippet: str = "",
    ) -> None:
        ws = get_workflow_settings()
        if not ws.get("enableStepLessonMemory", True):
            return
        try:
            project_id = state.CURRENT_PROJECT_ID or "default-proj"
            task_id = state.ACTIVE_SPRINT_TASK_ID
            files: List[str] = []
            if task_id:
                task = find_task_by_id(task_id)
                if task:
                    for f in task.get("files") or []:
                        if isinstance(f, dict) and f.get("path"):
                            files.append(str(f["path"]))
                        elif isinstance(f, str):
                            files.append(f)
            worked = ", ".join(sorted(tools_used)) if tools_used else "none"
            lesson = (
                f"Step stop={stop_reason or 'unknown'}; tools=[{worked}]. "
                f"{str(result_snippet or '')[:240]}"
            )
            self.memory.save_step_lesson(
                self.role,
                lesson=lesson,
                stop_reason=stop_reason or "",
                tools_used=sorted(tools_used),
                task_id=task_id,
                files=files[:12],
                project_id=project_id,
            )
        except Exception:
            pass

    def _append_observation_summary(
        self,
        messages: List[ChatMessage],
        batch: List[Tuple[str, Dict[str, Any], Any]],
    ) -> None:
        ws = get_workflow_settings()
        if not ws.get("enableObservationSummaries", True):
            return
        lines = ["=== OBSERVATION ==="]
        files_touched: List[str] = []
        hints: List[str] = []
        for tool_name, arguments, result in batch:
            ok = "ok" if getattr(result, "success", False) else "FAIL"
            if getattr(result, "duplicate_skip", False):
                ok = "skip"
            out = str(getattr(result, "tool_output", "") or "").replace("\n", " ")[:120]
            lines.append(f"- {tool_name}: {ok} — {out}")
            path = ""
            if isinstance(arguments, dict):
                path = str(arguments.get("path") or "")
            if path:
                files_touched.append(path)
            # Fold former per-tool system nudges into this one block.
            if getattr(result, "duplicate_skip", False):
                hints.append(
                    f"Already ran '{tool_name}' with identical args — change approach; do not repeat."
                )
            elif not getattr(result, "success", False):
                hint = (
                    f"Tool '{tool_name}' failed — do not repeat the same arguments."
                )
                if tool_name == "apply_patch":
                    hint += (
                        " Call read_file on the same path, then retry with exact old_text "
                        "from that result (not preloaded context)."
                    )
                hints.append(hint)
            elif tool_name == "read_file" and path:
                path_lower = path.lower().replace("\\", "/")
                dep = ""
                if path_lower.endswith("pubspec.yaml") or path_lower.endswith("package.json"):
                    dep = " Dependency file — apply_patch required next."
                hints.append(
                    f"read_file ok for '{path}' — apply_patch next with verbatim old_text.{dep}"
                )
            elif tool_name == "run_command":
                from backend.agents.tool_outcomes import parse_run_command_exit
                from backend.services.diagnostics_parser import parse_command_diagnostics

                llm_out = str(getattr(result, "tool_output", "") or "")
                exit_code, body = parse_run_command_exit(llm_out)
                command = str((arguments or {}).get("command") or "")
                diagnostics = parse_command_diagnostics(command, body or llm_out)
                if diagnostics:
                    try:
                        max_keep = int(ws.get("maxInCardLintFixes", 5))
                    except Exception:
                        max_keep = 5
                    hints.append(
                        f"Command returned {len(diagnostics)} problem(s) — fix at most "
                        f"{max_keep} highest-severity AC-relevant findings before re-running."
                    )
                elif exit_code is not None and exit_code > 0:
                    hints.append(
                        "Command non-zero exit — fix budgeted issues, then re-run once "
                        "(do not repeat without edits)."
                    )
        if files_touched:
            lines.append("files touched: " + ", ".join(dict.fromkeys(files_touched)))
        for hint in dict.fromkeys(hints):
            lines.append(f"→ {hint}")
        messages.append({"role": "system", "content": "\n".join(lines)})

    def _append_tool_messages(
        self,
        messages: List[ChatMessage],
        tool_name: str,
        arguments: Dict[str, Any],
        tool_output: str,
        success: bool,
        *,
        duplicate_skip: bool = False,
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
        # When observation summaries are on, nudges live in === OBSERVATION === only.
        ws = get_workflow_settings()
        if ws.get("enableObservationSummaries", True):
            return
        if duplicate_skip:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Already ran '{tool_name}' with identical args — use prior output; "
                        "change approach or edit files. Do not repeat the same command."
                    ),
                }
            )
            return
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
        elif tool_name == "read_file":
            path = str(arguments.get("path") or "?")
            path_lower = path.lower().replace("\\", "/")
            dep_hint = ""
            if path_lower.endswith("pubspec.yaml") or path_lower.endswith("package.json"):
                dep_hint = (
                    " This task requires dependency updates — call apply_patch now to add "
                    "the required plugins/dependencies. Do not respond with text."
                )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"read_file succeeded for '{path}'. Use apply_patch on this path next — "
                        "copy old_text verbatim from the read_file output above. "
                        "Do not stop until edits are written."
                        f"{dep_hint}"
                    ),
                }
            )
        elif tool_name == "run_command":
            exit_code, body = parse_run_command_exit(llm_output)
            command = str(arguments.get("command") or "")
            diagnostics = parse_command_diagnostics(command, body or llm_output)
            if diagnostics:
                try:
                    max_keep = int(get_workflow_settings().get("maxInCardLintFixes", 5))
                except Exception:
                    max_keep = 5
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Command returned {len(diagnostics)} problem(s). "
                            f"Fix at most {max_keep} highest-severity findings relevant to this "
                            "card's AC (in-card lint budget) with apply_patch/write_file before "
                            "re-running. Do not clear the whole project on this card — leftover "
                            "lint is split to related Backlog cards automatically."
                        ),
                    }
                )
            elif exit_code is not None and exit_code > 0:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Command completed with findings (non-zero exit). "
                            "Fix budgeted issues with apply_patch/write_file, then re-run "
                            "the lint command once. Do not repeat the same command without making changes."
                        ),
                    }
                )

    def _inject_ac_progress_after_write(self, messages: List[ChatMessage], task_id: Optional[str]) -> None:
        if not task_id:
            return
        try:
            from backend.services.step_diagnostics import build_card_work_snapshot

            snap = build_card_work_snapshot(task_id=task_id) or {}
            remaining = snap.get("remainingAcceptance") or snap.get("acceptanceRemaining")
            files = snap.get("files") or snap.get("filesTouched") or []
            ac_lines = []
            task = find_task_by_id(task_id)
            if task:
                for c in (task.get("acceptanceCriteria") or [])[:6]:
                    ac_lines.append(f"- {c}")
            block = ["=== PERCEIVE (after write) ===", "Re-check remaining work before more tools."]
            if ac_lines:
                block.append("Acceptance criteria:")
                block.extend(ac_lines)
            if remaining:
                block.append(f"Remaining: {str(remaining)[:400]}")
            if files:
                block.append(
                    "Files on card: "
                    + ", ".join(str(f) for f in (files if isinstance(files, list) else [])[:8])
                )
            messages.append({"role": "system", "content": "\n".join(block)})
        except Exception:
            pass

    def _chat_options(self) -> Dict[str, Any]:
        from backend.services.prompt_budget import resolve_ollama_num_ctx

        ws = get_workflow_settings()
        opts: Dict[str, Any] = {
            "temperature": 0.1,
            "num_ctx": resolve_ollama_num_ctx(self.role),
        }
        keep_alive = ws.get("ollamaKeepAlive")
        if keep_alive:
            opts["keep_alive"] = str(keep_alive)
        return opts

    @staticmethod
    def _is_context_overflow_error(error: str) -> bool:
        lower = error.lower()
        return "exceed_context" in lower or "context size" in lower

    @staticmethod
    def _classify_ollama_error(error: str) -> str:
        if ScrumAgent._is_context_overflow_error(error):
            return "context_overflow"
        lower = error.lower()
        if "timeout" in lower or "timed out" in lower:
            return "timeout"
        if any(k in lower for k in ("connection", "refused", "unreachable", "connect")):
            return "connection"
        return "other"

    def _context_overflow_message(self) -> str:
        from backend.services.prompt_budget import resolve_ollama_num_ctx

        num_ctx = resolve_ollama_num_ctx(self.role)
        return (
            f"Request exceeded Ollama context (num_ctx={num_ctx}). "
            "Increase Ollama context size in Workflow settings, or shorten the project brief / remove assigned skills."
        )

    def _single_chat_attempt(
        self,
        client: Client,
        messages: Sequence[ChatMessage],
        *,
        stream: bool,
        tools: Optional[Sequence[Dict[str, Any]]],
        iteration: int,
        task_id: Optional[str],
        agent_id: str,
        run_id: Optional[str],
        tool_names: List[str],
    ) -> Tuple[Optional[Any], Optional[str], Optional[str], int]:
        """Returns (result, error, error_type, duration_ms)."""
        from backend.services.llm_debug_log import append_llm_log_entry

        started = time.time()
        try:
            result = client.chat(
                model=self.model,
                messages=list(messages),
                tools=tools,
                stream=stream,
                options=self._chat_options(),
            )
            duration_ms = int((time.time() - started) * 1000)
            prompt_tokens = eval_tokens = total_tokens = 0
            tokens_reported = False
            if not stream and result is not None:
                from backend.services.agent_usage import extract_ollama_token_counts

                prompt_tokens, eval_tokens, total_tokens, tokens_reported = extract_ollama_token_counts(
                    result
                )
                self._last_token_usage = {
                    "promptTokens": prompt_tokens,
                    "evalTokens": eval_tokens,
                    "totalTokens": total_tokens,
                    "tokensReported": tokens_reported,
                }
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
                    agent_id=agent_id,
                    task_id=task_id,
                    run_id=run_id,
                    model=self.model,
                    iteration=iteration,
                    request_messages=messages,
                    tool_names=tool_names,
                    response_content=(msg.content or "") if msg else "",
                    response_tool_calls=tool_calls,
                    duration_ms=duration_ms,
                    memories_used=getattr(self, "_last_memories_used", None),
                    decisions_included=getattr(self, "_decisions_in_prompt", None),
                    prompt_tokens=prompt_tokens,
                    eval_tokens=eval_tokens,
                    total_tokens=total_tokens,
                    tokens_reported=tokens_reported,
                    prompt_unchanged_inject=getattr(self, "_prompt_unchanged_inject", False),
                    prompt_section=getattr(self, "_current_prompt_section", None),
                )
            return result, None, None, duration_ms
        except Exception as exc:
            last_error = str(exc)
            error_type = self._classify_ollama_error(last_error)
            duration_ms = int((time.time() - started) * 1000)
            append_llm_log_entry(
                agent=self.role,
                agent_id=agent_id,
                task_id=task_id,
                run_id=run_id,
                model=self.model,
                iteration=iteration,
                request_messages=messages,
                tool_names=tool_names,
                duration_ms=duration_ms,
                error=last_error,
                error_type=error_type,
                memories_used=getattr(self, "_last_memories_used", None),
                decisions_included=getattr(self, "_decisions_in_prompt", None),
                prompt_unchanged_inject=getattr(self, "_prompt_unchanged_inject", False),
                prompt_section=getattr(self, "_current_prompt_section", None),
            )
            return None, last_error, error_type, duration_ms

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

        client = self._get_client()
        max_retries = self._ollama_max_retries()
        delays = self._ollama_retry_delays()
        while len(delays) < max_retries:
            delays.append(delays[-1] if delays else 0)
        delays = delays[:max_retries]

        if agent_id is None:
            agent_id = next((aid for aid, a in AGENT_MAP.items() if a is self), "dev")
        tid = task_id or state.ACTIVE_SPRINT_TASK_ID
        active_run = get_active_run()
        run_id = active_run.run_id if active_run else None
        tool_names = [n for n in (t.get("function", {}).get("name") for t in (tools or []) if isinstance(t, dict)) if n]

        last_error: Optional[str] = None
        last_error_type: Optional[str] = None
        timeout_sec = int(self._ollama_timeout_sec())

        def _run_attempts(attempt_delays: List[int], *, phase: str) -> Optional[Any]:
            nonlocal last_error, last_error_type
            total = len(attempt_delays)
            for idx, delay in enumerate(attempt_delays):
                if delay:
                    time.sleep(delay)
                attempt_num = idx + 1
                result, err, err_type, duration_ms = self._single_chat_attempt(
                    client,
                    messages,
                    stream=stream,
                    tools=tools,
                    iteration=iteration,
                    task_id=tid,
                    agent_id=agent_id or "dev",
                    run_id=run_id,
                    tool_names=tool_names,
                )
                if result is not None:
                    self._last_chat_error = None
                    self._last_chat_error_type = None
                    return result
                last_error = err
                last_error_type = err_type
                if err_type == "context_overflow":
                    overflow_msg = self._context_overflow_message()
                    add_system_log(self.role, "error", overflow_msg)
                    self._last_chat_error = err
                    self._last_chat_error_type = err_type
                    return None
                reason = err_type or "error"
                if err_type == "timeout":
                    detail = f"timeout after {timeout_sec}s"
                else:
                    detail = (err or "unknown")[:120]
                prefix = f"Ollama {phase} attempt {attempt_num}/{total} failed ({reason}"
                add_system_log(self.role, "warning", f"{prefix}: {detail})")
            return None

        result = _run_attempts(delays, phase="")
        if result is not None:
            return result

        ws = get_workflow_settings()
        if (
            last_error_type != "context_overflow"
            and ws.get("ollamaCooldownRetryEnabled", True)
        ):
            cooldown = max(0, int(ws.get("ollamaCooldownRetrySec", 15)))
            extra_attempts = max(0, int(ws.get("ollamaCooldownRetryAttempts", 2)))
            if extra_attempts > 0:
                add_system_log(
                    self.role,
                    "info",
                    f"Ollama cooldown retry in {cooldown}s ({extra_attempts} more attempt(s))…",
                )
                if cooldown:
                    time.sleep(cooldown)
                extra_delays = [0] * extra_attempts
                result = _run_attempts(extra_delays, phase="cooldown")
                if result is not None:
                    return result

        self._last_chat_error = last_error
        self._last_chat_error_type = last_error_type
        summary = last_error or "unknown"
        add_system_log(
            self.role,
            "warning",
            f"All Ollama attempts failed — last error ({last_error_type or 'other'}): {summary[:200]}",
        )
        return None

    def _log_step_exit(self, message: str, log_type: str = "warning") -> None:
        # Agent loop stop: duplicate tools, max failures, max iterations, or step duration.
        if message.startswith(("Stopped:", "Timed out:", "Max tool iterations")):
            add_system_log(self.role, log_type, f"Agent loop stop: {message}")
        else:
            add_system_log(self.role, log_type, message)

    def _step_timeout_message(self, max_duration_sec: int) -> str:
        mins = max(1, int(round(max_duration_sec / 60)))
        return (
            f"Timed out: agent step exceeded {mins} min — stopping to avoid an unbounded loop. "
            "Resume with Sprint step or chat."
        )

    def _publish_work_progress(
        self,
        *,
        task_id: Optional[str],
        intent: str,
        status: Optional[str] = None,
        iteration: Optional[int] = None,
        max_iterations: Optional[int] = None,
        run_status: Optional[str] = None,
        current_tool: Optional[str] = None,
        current_tool_detail: Optional[str] = None,
        clear_tool: bool = False,
        publish_activity_event: bool = False,
        prompt_section: Optional[str] = None,
    ) -> None:
        """Emit intent + cardProgress on agent_run and sprint_progress."""
        from backend.services.step_diagnostics import build_card_work_snapshot

        card = build_card_work_snapshot(task_id=task_id) if task_id else None
        focus_ac: Optional[int] = None
        focus_sub: Optional[str] = None
        if task_id:
            active = find_task_by_id(task_id) or {}
            if active.get("focusMode") == "ac" and active.get("focusAcIndex") is not None:
                try:
                    focus_ac = int(active.get("focusAcIndex"))
                except (TypeError, ValueError):
                    pass
            if active.get("focusSubtaskId"):
                focus_sub = str(active.get("focusSubtaskId"))
        update_kwargs: Dict[str, Any] = {
            "intent": intent,
            "card_progress": card,
        }
        if prompt_section is not None:
            update_kwargs["prompt_section"] = prompt_section
        if focus_ac is not None:
            update_kwargs["focus_ac_index"] = focus_ac
        if focus_sub:
            update_kwargs["focus_subtask_id"] = focus_sub
        if run_status is not None:
            update_kwargs["status"] = run_status
        if iteration is not None:
            update_kwargs["iteration"] = iteration
        if max_iterations is not None:
            update_kwargs["max_iterations"] = max_iterations
        if clear_tool:
            update_kwargs["clear_tool"] = True
        elif current_tool is not None:
            update_kwargs["current_tool"] = current_tool
        if current_tool_detail is not None:
            update_kwargs["current_tool_detail"] = current_tool_detail
        elif clear_tool:
            update_kwargs["clear_tool_detail"] = True
        update_run(**update_kwargs)

        if task_id and state.SPRINT_PROGRESS_MAX:
            from backend.services.sprint_service import publish_sprint_progress

            active = find_task_by_id(task_id) or {}
            publish_sprint_progress(
                phase="sprint_step",
                step=state.SPRINT_PROGRESS_STEP or (iteration or 0),
                max_steps=state.SPRINT_PROGRESS_MAX,
                agent=self.role,
                task_id=task_id,
                task_title=str(active.get("title") or task_id),
                lane=get_task_lane(task_id) or "",
                status=status or intent,
                intent=intent,
                card_progress=card,
                focus_ac_index=focus_ac,
                focus_subtask_id=focus_sub,
                prompt_section=prompt_section,
            )
        if publish_activity_event and task_id and intent:
            from backend.agents.task_context import publish_activity

            publish_activity(
                task_id,
                "progress",
                intent,
                role=self.role,
                agent=self.role,
            )

    def _execute_single_tool_call(
        self,
        call: Any,
        *,
        task_id: Optional[str],
        agent_id: str,
        run_id: str,
        user_prompt: str,
        failed_tool_keys: List[Tuple[str, str]],
        successful_tool_keys: List[Tuple[str, str]],
        total_failures: List[int],
        max_tool_failures: int,
    ) -> Tuple[str, Dict[str, Any], ToolExecutionResult, Optional[str]]:
        """Returns (tool_name, arguments, result, early_stop_message)."""
        from backend.agents.tool_outcomes import sanitize_tool_args_for_log, summarize_tool_args
        from backend.services.step_diagnostics import build_live_intent

        tool_name = call.function.name
        arguments = normalize_tool_arguments(call.function.arguments)
        tool_summary = summarize_tool_args(tool_name, arguments)
        key = (tool_name, json.dumps(arguments, sort_keys=True, default=str))

        def _track_fingerprint(*, block: bool = False) -> None:
            if not task_id:
                return
            state.STEP_TOOL_FINGERPRINT_KEYS.append(key)
            live = find_task_by_id(task_id)
            if not live:
                return
            from backend.agents.tool_fingerprints import (
                block_tool_fingerprint_on_task,
                record_tool_fingerprint_on_task,
            )

            if block:
                block_tool_fingerprint_on_task(live, tool_name, arguments)
                state.STEP_TOOL_BLOCK_KEYS.append(key)
            else:
                record_tool_fingerprint_on_task(live, tool_name, arguments)

        # Hard-skip fingerprints blocked on a prior stuck step — never call execute_tool.
        from backend.services.duplicate_tool_policy import duplicate_cross_step_block_applies

        if task_id and duplicate_cross_step_block_applies(tool_name):
            from backend.agents.tool_fingerprints import is_tool_fingerprint_blocked

            live_task = find_task_by_id(task_id)
            if is_tool_fingerprint_blocked(live_task, tool_name, arguments):
                cmd = str((arguments or {}).get("command") or tool_summary)[:120]
                tool_output = (
                    f"[blocked fingerprint] Tool '{tool_name}' with these args was blocked "
                    "after a prior stuck step"
                    + (f" ({cmd})" if cmd else "")
                    + ". Change approach or edit files before retrying."
                )
                skip_intent = f"Blocked fingerprint {tool_name}"
                self._publish_work_progress(
                    task_id=task_id,
                    intent=skip_intent,
                    status=skip_intent,
                    run_status="thinking",
                    clear_tool=True,
                    publish_activity_event=True,
                )
                add_system_log(self.role, "info", skip_intent)
                with _FAILURE_LOCK:
                    successful_tool_keys.append(key)
                _track_fingerprint()
                _log_duplicate_skip(
                    agent=self.role,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_output=tool_output,
                    task_id=task_id,
                    run_id=run_id,
                    success=False,
                )
                safe_args = sanitize_tool_args_for_log(tool_name, arguments)
                result = ToolExecutionResult(
                    tool_name=tool_name,
                    arguments=arguments,
                    safe_args=safe_args,
                    tool_output=tool_output,
                    success=False,
                    duration_ms=0,
                    timestamp="",
                    agent=self.role,
                    agent_id=agent_id,
                    task_id=task_id,
                    source="agent",
                    run_id=run_id,
                )
                setattr(result, "duplicate_skip", True)
                return tool_name, arguments, result, None

        with _FAILURE_LOCK:
            same_success = successful_tool_keys.count(key)

        from backend.services.duplicate_tool_policy import (
            duplicate_in_step_hard_stop_applies,
            duplicate_in_step_soft_skip_applies,
        )

        if duplicate_in_step_hard_stop_applies(tool_name) and same_success >= SAME_ARGS_SUCCESS_LIMIT - 1:
            # Already succeeded once and skipped once (count >= 2) → stop like failure stuck-loop
            cmd_hint = ""
            if tool_name == "run_command" and isinstance(arguments, dict):
                cmd_hint = str(arguments.get("command") or "")[:80]
            stop_msg = (
                f"Stopped: tool '{tool_name}' succeeded repeatedly with identical arguments"
                + (f" ({cmd_hint})" if cmd_hint else "")
                + ". Change approach or edit files before retrying."
            )
            self._log_step_exit(stop_msg, "warning")
            self._publish_work_progress(
                task_id=task_id,
                intent=f"Stopped duplicate {tool_name}"
                + (f": {cmd_hint}" if cmd_hint else ""),
                status=stop_msg[:200],
                run_status="failed",
                clear_tool=True,
            )
            finish_run(status="failed", error=stop_msg)
            _track_fingerprint(block=True)
            _log_duplicate_skip(
                agent=self.role,
                tool_name=tool_name,
                arguments=arguments,
                tool_output=stop_msg,
                task_id=task_id,
                run_id=run_id,
                success=False,
            )
            safe_args = sanitize_tool_args_for_log(tool_name, arguments)
            result = ToolExecutionResult(
                tool_name=tool_name,
                arguments=arguments,
                safe_args=safe_args,
                tool_output=stop_msg,
                success=False,
                duration_ms=0,
                timestamp="",
                agent=self.role,
                agent_id=agent_id,
                task_id=task_id,
                source="agent",
                run_id=run_id,
            )
            return tool_name, arguments, result, stop_msg

        if duplicate_in_step_soft_skip_applies(tool_name) and same_success >= 1:
            from backend.services.tool_cache import get_cached_result

            cached = get_cached_result(tool_name, arguments)
            if cached:
                tool_output, success = cached
            else:
                cmd = str((arguments or {}).get("command") or tool_summary)[:120]
                tool_output = (
                    f"[skipped duplicate] Already ran '{tool_name}' with identical args"
                    + (f" ({cmd})" if cmd else "")
                    + ". Use prior output; change approach or edit files."
                )
                success = True
            skip_intent = f"Skipped duplicate {tool_name}"
            if tool_name == "run_command":
                skip_intent = f"Skipped duplicate run_command: {str(arguments.get('command') or '')[:100]}"
            self._publish_work_progress(
                task_id=task_id,
                intent=skip_intent,
                status=skip_intent,
                run_status="thinking",
                clear_tool=True,
                publish_activity_event=True,
            )
            add_system_log(self.role, "info", skip_intent)
            with _FAILURE_LOCK:
                successful_tool_keys.append(key)
            _track_fingerprint()
            _log_duplicate_skip(
                agent=self.role,
                tool_name=tool_name,
                arguments=arguments,
                tool_output=tool_output,
                task_id=task_id,
                run_id=run_id,
            )
            safe_args = sanitize_tool_args_for_log(tool_name, arguments)
            result = ToolExecutionResult(
                tool_name=tool_name,
                arguments=arguments,
                safe_args=safe_args,
                tool_output=tool_output,
                success=success,
                duration_ms=0,
                timestamp="",
                agent=self.role,
                agent_id=agent_id,
                task_id=task_id,
                source="agent",
                run_id=run_id,
            )
            # Marker consumed by _process_tool_calls
            setattr(result, "duplicate_skip", True)
            return tool_name, arguments, result, None

        def _on_awaiting(name: str) -> None:
            self._publish_work_progress(
                task_id=task_id,
                intent=build_live_intent(
                    phase="tool",
                    tool_name=name,
                    tool_summary=tool_summary,
                ),
                status=f"Awaiting approval: {name}",
                run_status="awaiting_approval",
                current_tool=name,
                current_tool_detail=tool_summary,
            )

        def _on_executing(name: str) -> None:
            self._publish_work_progress(
                task_id=task_id,
                intent=build_live_intent(
                    phase="tool",
                    tool_name=name,
                    tool_summary=tool_summary,
                ),
                status=f"Running {name}: {tool_summary}"[:200],
                run_status="tool_executing",
                current_tool=name,
                current_tool_detail=tool_summary,
            )

        result = execute_tool(
            agent_id,
            tool_name,
            arguments,
            task_id=task_id,
            source="agent",
            skip_approval=False,
            run_id=run_id,
            user_prompt=user_prompt,
            on_awaiting_approval=_on_awaiting,
            on_tool_executing=_on_executing,
        )
        update_run(status="thinking", clear_tool=True)

        if result.pending_approval:
            stop_msg = result.tool_output
            finish_run(status="awaiting_approval", error=stop_msg)
            return tool_name, arguments, result, stop_msg

        if result.success and not result.pending_approval:
            with _FAILURE_LOCK:
                successful_tool_keys.append(key)
            _track_fingerprint()

        if not result.success and not result.pending_approval:
            with _FAILURE_LOCK:
                total_failures[0] += 1
                failed_tool_keys.append(key)
                same_count = failed_tool_keys.count(key)
                fail_total = total_failures[0]
            _track_fingerprint()
            if same_count >= SAME_ARGS_FAILURE_LIMIT:
                stop_msg = (
                    f"Stopped: tool '{tool_name}' failed repeatedly with the same arguments. "
                    f"Last error: {result.tool_output[:200]}"
                )
                self._log_step_exit(stop_msg, "error")
                finish_run(status="failed", error=stop_msg)
                _track_fingerprint(block=True)
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

    def _process_tool_calls(
        self,
        message: Message,
        messages: List[ChatMessage],
        user_prompt: str,
        failed_tool_keys: List[Tuple[str, str]],
        successful_tool_keys: List[Tuple[str, str]],
        total_failures: List[int],
        max_tool_failures: int,
        *,
        iteration: int,
        max_iterations: int,
        tools_used: set[str],
    ) -> Optional[str]:
        """Process tool calls; return early-stop message when limits exceeded."""
        messages.append(assistant_message_to_chat_dict(message))
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
                        successful_tool_keys=successful_tool_keys,
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
                successful_tool_keys=successful_tool_keys,
                total_failures=total_failures,
                max_tool_failures=max_tool_failures,
            )

        for call in all_calls:
            tool_name, arguments, result, early_stop = results_by_id[id(call)]
            if early_stop:
                return early_stop
            tools_used.add(tool_name)
            live_task = find_task_by_id(task_id) if task_id else None
            if live_task:
                from backend.services.task_working_context import (
                    record_tool_working_context,
                    save_task_fact_memory,
                )

                record_tool_working_context(
                    live_task,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_output=result.tool_output,
                    success=result.success,
                )
                if tool_name == "run_command":
                    cmd = str(arguments.get("command") or "")[:160]
                    save_task_fact_memory(
                        task_id=str(task_id),
                        agent_role=self.role,
                        content=f"{cmd} → {'ok' if result.success else 'FAIL'}: {(result.tool_output or '')[:300]}",
                        category="fact",
                    )
                elif self.role == "QA Tester" and tool_name in ("run_test", "run_command"):
                    save_task_fact_memory(
                        task_id=str(task_id),
                        agent_role=self.role,
                        content=f"QA {tool_name}: {'pass' if result.success else 'fail'} — {(result.tool_output or '')[:300]}",
                        category="verification",
                    )
            self._append_tool_messages(
                messages,
                tool_name,
                arguments,
                result.tool_output,
                result.success,
                duplicate_skip=bool(getattr(result, "duplicate_skip", False)),
            )

        batch = [
            (
                results_by_id[id(call)][0],
                results_by_id[id(call)][1],
                results_by_id[id(call)][2],
            )
            for call in all_calls
        ]
        self._append_observation_summary(messages, batch)
        if any(
            results_by_id[id(call)][0] in ("write_file", "apply_patch")
            and results_by_id[id(call)][2].success
            for call in all_calls
        ):
            self._inject_ac_progress_after_write(messages, task_id)

        tool_summary = ", ".join(
            call.function.name for call in all_calls if hasattr(call, "function")
        )
        next_iter = min(iteration + 1, max_iterations)
        add_system_log(
            self.role,
            "info",
            f"Tool batch done ({tool_summary}); continuing to LLM iteration {next_iter}/{max_iterations}",
        )
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
        ws = get_workflow_settings()
        max_tool_failures = int(ws.get("maxToolFailuresPerStep", 5))
        max_duration_sec = int(ws.get("maxAgentStepDurationSec", 2700) or 2700)
        step_started_mono = time.monotonic()
        self._mid_step_backup_switched = False
        try:
            if getattr(state, "SPRINT_STEP_STARTED_MONO", None) is None:
                state.SPRINT_STEP_STARTED_MONO = step_started_mono
        except Exception:
            pass
        messages: List[ChatMessage] = [
            {"role": "system", "content": self._build_system_content()},
            {"role": "user", "content": self._build_user_content(user_prompt)},
        ]
        rotation_enabled = bool(getattr(state, "SPRINT_PROMPT_ROTATION_ENABLED", False))
        rotation_blocks: List[str] = list(getattr(state, "SPRINT_PROMPT_ROTATION_BLOCKS", None) or [])
        rotation_names: List[str] = list(getattr(state, "SPRINT_PROMPT_ROTATION_NAMES", None) or [])
        fixed_prefix = str(getattr(state, "SPRINT_PROMPT_FIXED_PREFIX", "") or "")
        fixed_suffix = str(getattr(state, "SPRINT_PROMPT_FIXED_SUFFIX", "") or "")
        bundle_msg_index: Optional[int] = None
        from backend.services.llm_context import format_prompt_bundle_system_content

        if rotation_enabled and rotation_blocks and fixed_prefix:
            stable_user = "\n\n".join(part for part in (fixed_prefix, fixed_suffix) if part)
            messages[1] = {"role": "user", "content": self._build_user_content(stable_user)}
            b0 = rotation_blocks[0]
            bname0 = rotation_names[0] if rotation_names else "bundle_0"
            messages.append(
                {
                    "role": "system",
                    "content": format_prompt_bundle_system_content(bname0, b0),
                }
            )
            bundle_msg_index = 2

        def _apply_rotation_for_iteration(iteration: int) -> Optional[str]:
            if bundle_msg_index is not None:
                idx = (iteration - 1) % len(rotation_blocks)
                bundle_name = rotation_names[idx] if idx < len(rotation_names) else f"bundle_{idx}"
                rot = rotation_blocks[idx]
                messages[bundle_msg_index]["content"] = format_prompt_bundle_system_content(
                    bundle_name, rot
                )
                return bundle_name
            if not rotation_enabled or not rotation_blocks or not fixed_prefix:
                return None
            idx = (iteration - 1) % len(rotation_blocks)
            bundle_name = rotation_names[idx] if idx < len(rotation_names) else f"bundle_{idx}"
            rot = rotation_blocks[idx]
            messages[1]["content"] = "\n\n".join(
                part for part in (fixed_prefix, rot, fixed_suffix) if part
            )
            return bundle_name

        last_llm_fingerprint = ""
        self._prompt_unchanged_inject = False

        failed_tool_keys: List[Tuple[str, str]] = []
        successful_tool_keys: List[Tuple[str, str]] = []
        total_failures: List[int] = [0]
        tools_used: set[str] = set()
        task_id = state.ACTIVE_SPRINT_TASK_ID
        state.STEP_TOOL_FINGERPRINT_KEYS = []
        state.STEP_TOOL_BLOCK_KEYS = []
        if task_id:
            active_task = find_task_by_id(task_id)
            if active_task:
                self._decisions_in_prompt = min(len(active_task.get("decisions") or []), 8)
                from backend.agents.tool_fingerprints import seed_tool_keys_from_task

                seeded_success, seeded_fail = seed_tool_keys_from_task(active_task)
                successful_tool_keys.extend(seeded_success)
                failed_tool_keys.extend(seeded_fail)
            start_run(task_id, self.role, max_iterations=max_iterations)

        from backend.services.step_diagnostics import (
            build_live_intent,
            log_event,
            set_llm_iterations_max,
        )

        set_llm_iterations_max(max_iterations)
        pending_lesson: Optional[Tuple[str, set, str]] = None

        try:
            for iteration in range(1, max_iterations + 1):
                elapsed = time.monotonic() - step_started_mono
                if max_duration_sec > 0 and elapsed >= max_duration_sec:
                    stop_msg = self._step_timeout_message(max_duration_sec)
                    add_system_log(self.role, "warning", stop_msg)
                    self._log_step_exit(stop_msg, "warning")
                    log_event("step_timeout", stop_msg)
                    try:
                        from backend.services.phone_notify import notify_if_enabled

                        notify_if_enabled(
                            "step_timeout",
                            "Agent step timed out",
                            f"{self.role}: {stop_msg[:400]}",
                            task_id=task_id,
                        )
                    except Exception:
                        pass
                    finish_run(status="failed", error=stop_msg)
                    pending_lesson = ("step_timeout", set(tools_used), stop_msg)
                    return stop_msg
                if task_id and is_task_done(task_id) and not state.ALLOW_DONE_RETRY:
                    stop_msg = "Stopped: task already Done"
                    add_system_log(self.role, "info", stop_msg)
                    self._log_step_exit(stop_msg, "info")
                    finish_run(status="completed")
                    pending_lesson = ("task_done", set(tools_used), stop_msg)
                    return stop_msg
                intent = build_live_intent(
                    phase="thinking",
                    iteration=iteration,
                    max_iterations=max_iterations,
                )
                bundle_name = _apply_rotation_for_iteration(iteration)
                from backend.services.llm_context import maybe_inject_unchanged_prompt_progress

                last_llm_fingerprint, injected = maybe_inject_unchanged_prompt_progress(
                    messages,
                    iteration=iteration,
                    last_fingerprint=last_llm_fingerprint,
                )
                self._prompt_unchanged_inject = injected
                if injected:
                    add_system_log(
                        self.role,
                        "info",
                        f"Prompt unchanged vs iter {iteration - 1} — injected step progress summary",
                    )
                self._current_prompt_section = bundle_name
                self._publish_work_progress(
                    task_id=task_id,
                    intent=intent,
                    status=f"LLM iter {iteration}/{max_iterations}",
                    iteration=iteration,
                    max_iterations=max_iterations,
                    run_status="thinking",
                    clear_tool=True,
                    publish_activity_event=True,
                    prompt_section=bundle_name,
                )
                add_system_log(
                    self.role,
                    "info",
                    f"LLM iteration {iteration}/{max_iterations}",
                )
                from backend.services.llm_context import prune_messages_if_needed

                prune_messages_if_needed(messages)
                await_intent = build_live_intent(
                    phase="awaiting_ollama",
                    iteration=iteration,
                    max_iterations=max_iterations,
                    model=str(self.model or ""),
                )
                self._publish_work_progress(
                    task_id=task_id,
                    intent=await_intent,
                    status=await_intent,
                    iteration=iteration,
                    max_iterations=max_iterations,
                    run_status="thinking",
                )
                add_system_log(
                    self.role,
                    "info",
                    f"Waiting for model (Ollama) — iter {iteration}/{max_iterations}, "
                    f"model={self.model} num_ctx={self._chat_options().get('num_ctx')} "
                    f"keep_alive={get_workflow_settings().get('ollamaKeepAlive', '30m')} "
                    f"(LLM call in flight)",
                )
                log_event(
                    "ollama_wait",
                    f"iter {iteration}/{max_iterations} model={self.model}",
                )
                ollama_started = time.time()
                ollama_wait_done = threading.Event()

                def _tick_ollama_wait() -> None:
                    while not ollama_wait_done.wait(15):
                        elapsed = int(time.time() - ollama_started)
                        tick_intent = build_live_intent(
                            phase="awaiting_ollama",
                            iteration=iteration,
                            max_iterations=max_iterations,
                            model=str(self.model or ""),
                            elapsed_sec=elapsed,
                        )
                        self._publish_work_progress(
                            task_id=task_id,
                            intent=tick_intent,
                            status=tick_intent,
                            iteration=iteration,
                            max_iterations=max_iterations,
                            run_status="thinking",
                        )

                ticker = threading.Thread(
                    target=_tick_ollama_wait,
                    name="ollama-wait-ticker",
                    daemon=True,
                )
                ticker.start()
                try:
                    response = self._chat(
                        messages,
                        tools=tools or None,
                        iteration=iteration,
                        task_id=task_id,
                    )
                finally:
                    ollama_wait_done.set()
                ollama_duration_ms = int((time.time() - ollama_started) * 1000)
                from backend.services.step_diagnostics import log_ollama_call

                if response is None:
                    err_type = getattr(self, "_last_chat_error_type", None) or "unavailable"
                    log_ollama_call(
                        iteration,
                        duration_ms=ollama_duration_ms,
                        error="unavailable",
                        error_type=err_type,
                    )
                    self._log_step_exit("Ollama unavailable — SIMULATION_FALLBACK", "warning")
                    finish_run(status="failed", error="SIMULATION_FALLBACK")
                    pending_lesson = ("ollama_unavailable", set(tools_used), "SIMULATION_FALLBACK")
                    return "SIMULATION_FALLBACK"

                message = response.message
                recovered_tool_names, message = apply_tool_call_recovery(
                    message, self.registry.tool_names()
                )
                if recovered_tool_names:
                    add_system_log(
                        self.role,
                        "info",
                        f"Recovered tool calls from fenced/quoted content: {recovered_tool_names}",
                    )
                    log_event(
                        "tool_calls_recovered_from_content",
                        ", ".join(recovered_tool_names),
                    )
                tool_call_names = (
                    [tc.function.name for tc in message.tool_calls]
                    if message.tool_calls
                    else []
                )
                text_chars = len((message.content or "").strip())
                if tool_call_names:
                    add_system_log(
                        self.role,
                        "info",
                        f"Ollama responded in {ollama_duration_ms}ms — tools={tool_call_names}",
                    )
                else:
                    add_system_log(
                        self.role,
                        "info",
                        f"Ollama responded in {ollama_duration_ms}ms — text={text_chars} chars",
                    )
                log_ollama_call(
                    iteration,
                    duration_ms=ollama_duration_ms,
                    tool_calls=tool_call_names,
                    text_chars=text_chars,
                    prompt_tokens=int((getattr(self, "_last_token_usage", None) or {}).get("promptTokens") or 0),
                    eval_tokens=int((getattr(self, "_last_token_usage", None) or {}).get("evalTokens") or 0),
                    total_tokens=int((getattr(self, "_last_token_usage", None) or {}).get("totalTokens") or 0),
                    tokens_reported=bool(
                        (getattr(self, "_last_token_usage", None) or {}).get("tokensReported")
                    ),
                )
                if message.tool_calls:
                    early_stop = self._process_tool_calls(
                        message,
                        messages,
                        user_prompt,
                        failed_tool_keys,
                        successful_tool_keys,
                        total_failures,
                        max_tool_failures,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        tools_used=tools_used,
                    )
                    if early_stop:
                        reason = "tool_failure_stop"
                        low = early_stop.lower()
                        if "duplicate" in low or "same args" in low:
                            reason = "duplicate_tool"
                        elif "approval" in low:
                            reason = "awaiting_approval"
                        pending_lesson = (reason, set(tools_used), early_stop)
                        return early_stop
                    if task_id and is_task_done(task_id) and not state.ALLOW_DONE_RETRY:
                        stop_msg = "Stopped: task already Done"
                        add_system_log(self.role, "info", stop_msg)
                        self._log_step_exit(stop_msg, "info")
                        finish_run(status="completed")
                        pending_lesson = ("task_done", set(tools_used), stop_msg)
                        return stop_msg
                    continue

                content = unwrap_llm_text((message.content or "")).strip()
                if content and _dev_step_needs_more_tools(tools_used, task_id):
                    if task_id and is_task_done(task_id) and not state.ALLOW_DONE_RETRY:
                        stop_msg = "Stopped: task already Done"
                        add_system_log(self.role, "info", stop_msg)
                        self._log_step_exit(stop_msg, "info")
                        finish_run(status="completed")
                        return stop_msg
                    if iteration >= max_iterations:
                        max_msg = "Max tool iterations reached without completing the task."
                        add_system_log(
                            self.role,
                            "warning",
                            "Model returned text-only on final iteration (not a tool). "
                            "Not saved to backlog or memory — apply_patch required.",
                        )
                        log_event("text_rejected", content[:200])
                        write_tools = tools_used & {"write_file", "apply_patch"}
                        if tools_used and not write_tools:
                            tool_list = ", ".join(sorted(tools_used))
                            add_system_log(
                                self.role,
                                "warning",
                                f"Step ended after tools ({tool_list}) with no write_file or apply_patch",
                            )
                        add_system_log(
                            self.role,
                            "info",
                            f"Step exit: max_iterations tools=[{', '.join(sorted(tools_used)) or 'none'}]",
                        )
                        log_event("max_iterations", max_msg)
                        from backend.services.step_diagnostics import (
                            build_step_progress,
                            store_step_progress,
                        )

                        store_step_progress(
                            build_step_progress(
                                task_id=task_id,
                                iterations_used=iteration,
                                iterations_max=max_iterations,
                                tools_used=tools_used,
                                failed_tool_keys=failed_tool_keys,
                            )
                        )
                        self._log_step_exit(max_msg, "warning")
                        finish_run(status="failed", error=max_msg)
                        pending_lesson = ("max_iterations", set(tools_used), max_msg)
                        return max_msg

                    messages.append({"role": "assistant", "content": content})
                    from backend.services.step_diagnostics import get_active_trace

                    trace = get_active_trace()
                    plan_n = (trace.plan_rejections if trace else 0) + (
                        1 if _looks_like_plan_response(content) else 0
                    )
                    text_n = (trace.text_rejections if trace else 0) + (
                        0 if _looks_like_plan_response(content) else 1
                    )
                    next_iter = min(iteration + 1, max_iterations)
                    if _looks_like_plan_response(content):
                        add_system_log(
                            self.role,
                            "warning",
                            f"Plan-only response rejected ({plan_n} plan / {text_n} text rejections) — "
                            f"continuing to iter {next_iter}/{max_iterations}. "
                            "Text is not a tool, backlog item, or memory entry.",
                        )
                        log_event("plan_rejected", content[:200])
                        reject_label = "plan-only"
                    else:
                        add_system_log(
                            self.role,
                            "warning",
                            f"Text-only response rejected ({plan_n} plan / {text_n} text rejections) — "
                            f"continuing to iter {next_iter}/{max_iterations}. "
                            "Not saved to backlog or memory — apply_patch required.",
                        )
                        log_event("text_rejected", content[:200])
                        reject_label = "text-only"
                    reject_intent = build_live_intent(
                        phase="plan_reject" if reject_label == "plan-only" else "text_reject",
                        iteration=next_iter,
                        max_iterations=max_iterations,
                        reject_label=reject_label,
                    )
                    self._publish_work_progress(
                        task_id=task_id,
                        intent=reject_intent,
                        status=reject_intent,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        run_status="thinking",
                        publish_activity_event=True,
                    )
                    messages.append({"role": "system", "content": _PLAN_REJECTION_MESSAGE})
                    # Micro-reflect: force a concrete next tool after repeated rejects.
                    if (plan_n + text_n) >= 2:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "=== REFLECT ===\n"
                                    "Hypothesis: prior replies were plan/text-only. "
                                    "Next message MUST be a concrete tool call "
                                    "(apply_patch or write_file preferred). "
                                    "State one-line goal in the tool args path/content only — no plans."
                                ),
                            }
                        )
                    # Mid-step backup switch after repeated plan/text rejects.
                    if (
                        self.role == "Developer"
                        and (plan_n + text_n) >= 2
                        and not getattr(self, "_mid_step_backup_switched", False)
                    ):
                        try:
                            from backend.services.backup_model import (
                                arm_backup_for_agent,
                                backup_model,
                                primary_model,
                            )

                            tid = state.ACTIVE_SPRINT_TASK_ID
                            board_task = find_task_by_id(tid) if tid else None
                            if board_task:
                                backup = backup_model("dev")
                                primary = primary_model("dev")
                                if backup and backup != primary and backup != str(self.model or ""):
                                    armed = arm_backup_for_agent(
                                        "dev",
                                        board_task,
                                        reason=f"mid-step plan/text rejects ({plan_n}/{text_n})",
                                        force=True,
                                    )
                                    if armed or backup:
                                        try:
                                            from backend.services.ollama_warmup import (
                                                maybe_vram_unload_primary,
                                            )

                                            maybe_vram_unload_primary(primary, backup=backup)
                                        except Exception:
                                            pass
                                        self.model = backup
                                        self._mid_step_backup_switched = True
                                        add_system_log(
                                            self.role,
                                            "info",
                                            f"Mid-step backup switch → {backup} "
                                            f"(after {plan_n} plan / {text_n} text rejects)",
                                        )
                        except Exception:
                            pass
                    continue

                if task_id and content:
                    record_task_transcript(
                        task_id,
                        "assistant",
                        content,
                        agent=self.role,
                    )
                write_tools = tools_used & {"write_file", "apply_patch"}
                if tools_used and not write_tools:
                    tool_list = ", ".join(sorted(tools_used))
                    add_system_log(
                        self.role,
                        "warning",
                        f"Step ended after tools ({tool_list}) with no write_file or apply_patch",
                    )
                exit_reason = "completed_with_writes" if write_tools else "completed_text_only"
                add_system_log(
                    self.role,
                    "info",
                    f"Step exit: {exit_reason} tools=[{', '.join(sorted(tools_used)) or 'none'}]",
                )
                finish_run(status="completed")
                pending_lesson = (exit_reason, set(tools_used), content or "")
                return content or "Task completed."

            max_msg = "Max tool iterations reached without completing the task."
            write_tools = tools_used & {"write_file", "apply_patch"}
            if tools_used and not write_tools:
                tool_list = ", ".join(sorted(tools_used))
                add_system_log(
                    self.role,
                    "warning",
                    f"Step ended after tools ({tool_list}) with no write_file or apply_patch",
                )
            add_system_log(
                self.role,
                "warning",
                f"Step exit: max_iterations tools=[{', '.join(sorted(tools_used)) or 'none'}]",
            )
            log_event("max_iterations", max_msg)
            from backend.services.step_diagnostics import (
                build_step_progress,
                store_step_progress,
            )

            store_step_progress(
                build_step_progress(
                    task_id=task_id,
                    iterations_used=max_iterations,
                    iterations_max=max_iterations,
                    tools_used=tools_used,
                    failed_tool_keys=failed_tool_keys,
                )
            )
            self._log_step_exit(max_msg, "warning")
            finish_run(status="failed", error=max_msg)
            pending_lesson = ("max_iterations", set(tools_used), max_msg)
            return max_msg
        except Exception as exc:
            finish_run(status="failed", error=str(exc))
            pending_lesson = ("exception", set(tools_used), str(exc))
            raise
        finally:
            if pending_lesson:
                reason, tools, snip = pending_lesson
                self._save_step_lesson(reason, tools, snip)
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
