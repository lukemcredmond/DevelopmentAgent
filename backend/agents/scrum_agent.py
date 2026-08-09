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
PATH_TOOL_SAME_ARGS_FAILURE_LIMIT = 2
PATH_TOOL_NAMES = frozenset({"read_file", "list_dir", "grep", "glob_file_search", "apply_patch", "write_file"})


def _task_ac_description_text(task: Optional[Dict[str, Any]]) -> str:
    if not task:
        return ""
    normalize_task(task)
    parts = [str(task.get("title") or ""), str(task.get("description") or "")]
    for ac in task.get("acceptanceCriteria") or []:
        parts.append(str(ac))
    return " ".join(parts).lower()


def _task_suggests_dependency_verify_only(task: Optional[Dict[str, Any]]) -> bool:
    """True when AC looks like check/verify dependency, not add/update."""
    text = _task_ac_description_text(task)
    if not text:
        return False
    verify = (
        "verify",
        "confirm",
        "check",
        "installed",
        "present",
        "exists",
        "already",
        "declared",
        "listed",
        "contains",
        "includes",
        "version",
    )
    edit = (
        "add ",
        "install",
        "update",
        "upgrade",
        "include",
        "pin ",
        "bump",
        "ensure dependency",
        "add dependency",
    )
    has_verify = any(m in text for m in verify)
    has_edit = any(m in text for m in edit)
    return has_verify and not has_edit


def _read_file_followup_system_message(
    path: str,
    *,
    task: Optional[Dict[str, Any]] = None,
) -> str:
    path_lower = path.lower().replace("\\", "/")
    is_manifest = path_lower.endswith("pubspec.yaml") or path_lower.endswith("package.json")
    if is_manifest and _task_suggests_dependency_verify_only(task):
        return (
            f"read_file succeeded for '{path}'. Use the tool message above to answer the AC "
            "(find package names/versions such as firebase_auth). "
            "Do not apply_patch unless the AC requires changing dependencies — "
            "then update_board or run grep if you still need a smaller snippet."
        )
    dep_hint = ""
    if is_manifest:
        dep_hint = (
            " This task requires dependency updates — call apply_patch now to add "
            "the required plugins/dependencies. Do not respond with text."
        )
    return (
        f"read_file succeeded for '{path}'. Use apply_patch on this path next — "
        "copy old_text verbatim from the read_file output above. "
        "Do not stop until edits are written."
        f"{dep_hint}"
    )


def _manifest_read_observation_hint(path_lower: str, task: Optional[Dict[str, Any]]) -> str:
    if not (path_lower.endswith("pubspec.yaml") or path_lower.endswith("package.json")):
        return ""
    if _task_suggests_dependency_verify_only(task):
        return " Dependency manifest — answer AC from tool output; patch only if AC requires edits."
    return " Dependency file — apply_patch required next."
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


def _duplicate_loop_stop_message(
    tool_name: str,
    arguments: Dict[str, Any],
    same_success: int,
) -> str:
    from backend.services.duplicate_tool_policy import (
        _duplicate_args_summary,
        _suggested_next_after_duplicate,
    )

    summary = _duplicate_args_summary(tool_name, arguments)
    args_clause = f" ({summary})" if summary else ""
    return (
        f"Stopped: loop detected — '{tool_name}' invoked {same_success + 1} time(s) this step "
        f"with identical arguments{args_clause}. Tool output and workspace are unchanged. "
        f"{_suggested_next_after_duplicate(tool_name, arguments)}"
    )


def _resolve_in_step_duplicate_replay(
    tool_name: str,
    arguments: Dict[str, Any],
    live_task: Optional[Dict[str, Any]],
    same_success: int,
    *,
    limit: int = SAME_ARGS_SUCCESS_LIMIT,
) -> Optional[Tuple[str, bool]]:
    from backend.services.duplicate_tool_policy import (
        apply_duplicate_loop_breaker_to_output,
        duplicate_loop_should_hard_stop,
    )
    from backend.services.tool_cache import resolve_duplicate_replay

    if duplicate_loop_should_hard_stop(same_success, limit=limit):
        return None
    replay = resolve_duplicate_replay(tool_name, arguments, live_task)
    if not replay:
        return None
    body, success = replay
    body = apply_duplicate_loop_breaker_to_output(
        tool_name,
        arguments,
        body,
        identical_prior_successes=same_success,
        limit=limit,
    )
    return body, success


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

_PO_IDLE_GREETING_MARKERS = (
    "ready to act",
    "ready to assist",
    "please provide the product brief",
    "please provide the brief",
    "provide the product brief",
    "provide the brief",
    "share the product brief",
    "share your brief",
    "any new feature",
    "new feature requests",
    "how can i assist",
    "how may i assist",
)

_PO_BOARD_TOOLS = frozenset({"update_board", "add_backlog_tasks", "add_subtasks"})
_PO_READONLY_TOOLS = frozenset(
    {"list_dir", "grep", "glob_file_search", "read_file", "search_code", "semantic_search", "graph_query"}
)

_PO_IDLE_REJECTION_MESSAGE = (
    "You are already in an active project step. Task Detail and the project brief are in the "
    "user message above; tool results (list_dir, grep, read_file) are in prior tool messages. "
    "Do not ask the user to paste the brief again. Use those results and call update_board, "
    "add_backlog_tasks, add_subtasks, or reply with the JSON format requested in Task Detail."
)

_PO_CLARIFICATION_PLAN_REJECTION = (
    "You are the Product Owner clarifying requirements for the Developer — not implementing code. "
    "Do not list development steps or tell the Developer how to build the feature. "
    'Reply with a JSON object: {"description": "...", "acceptanceCriteria": ["..."], '
    '"briefAddition": "..."} then call update_board to move the task back to In Progress.'
)


def _looks_like_po_implementation_plan(content: str, task_id: Optional[str] = None) -> bool:
    """Dev-style step lists while acting as PO (especially Needs PO clarification)."""
    if _looks_like_po_work_product(content):
        return False
    lower = (content or "").lower()
    if "follow these steps" in lower or "follow theses steps" in lower:
        return True
    if "develop the" in lower and re.search(r"(?m)^\s*\d+\.", content or ""):
        return True
    lane = get_task_lane(task_id) if task_id else get_task_lane(state.ACTIVE_SPRINT_TASK_ID or "")
    if lane == "Needs PO":
        return _looks_like_plan_response(content)
    return False


def _po_rejection_system_message(content: str, task_id: Optional[str]) -> str:
    lane = get_task_lane(task_id) if task_id else ""
    if lane == "Needs PO" and _looks_like_po_implementation_plan(content, task_id):
        return _PO_CLARIFICATION_PLAN_REJECTION
    return _PO_IDLE_REJECTION_MESSAGE


def _looks_like_po_idle_greeting(content: str) -> bool:
    lower = (content or "").lower()
    if not lower.strip():
        return False
    if any(m in lower for m in _PO_IDLE_GREETING_MARKERS):
        return True
    if "product owner" in lower and ("provide" in lower or "share" in lower) and "brief" in lower:
        return True
    return False


def _looks_like_po_work_product(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    lower = text.lower()
    if '"acceptancecriteria"' in lower or '"description"' in lower:
        return True
    if text.startswith("[") and ("title" in lower or "acceptance" in lower):
        return True
    if text.startswith("{") and ("acceptancecriteria" in lower or "executionplan" in lower):
        return True
    return False


def _po_step_should_reject_text_only(
    content: str,
    tools_used: set[str],
    task_id: Optional[str],
) -> bool:
    """PO must not exit on onboarding chat after exploration tools or with task context."""
    if state.ACTIVE_SPRINT_AGENT != "Product Owner":
        return False
    if not (content or "").strip():
        return False
    if _looks_like_po_work_product(content):
        return False
    if task_id and get_task_lane(task_id) == "Needs PO" and _looks_like_po_implementation_plan(content, task_id):
        return True
    if _looks_like_po_idle_greeting(content):
        return bool(task_id or tools_used)
    explored = bool(tools_used & _PO_READONLY_TOOLS)
    acted = bool(tools_used & _PO_BOARD_TOOLS)
    if explored and not acted:
        return True
    return False


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
        self._step_num_ctx: Optional[int] = None

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

        from backend.services.prompt_budget import skills_context_max_chars

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

        from backend.services.prompt_profile import is_local_slm_profile

        if is_local_slm_profile():
            names = ", ".join(skill_files[:6])
            suffix = "…" if len(skill_files) > 6 else ""
            return f"\n=== ASSIGNED SKILLS (names only — no full skill text in local SLM mode) ===\n{names}{suffix}\n"

        max_chars = skills_context_max_chars(self._effective_num_ctx())
        if max_chars <= 0:
            return ""
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
        related_memories = []
        from backend.services.prompt_profile import (
            is_local_slm_profile,
            local_slm_sprint_preload_enabled,
        )

        if not is_local_slm_profile():
            related_memories = self.memory.search(
                self.role,
                query,
                limit=3,
                project_id=project_id,
                include_all_agents=True,
                prefer_categories=prefer,
            )
        elif local_slm_sprint_preload_enabled():
            from backend.services.prompt_budget import LOCAL_SLM_MEMORY_CHARS

            related_memories = self.memory.search(
                self.role,
                query,
                limit=2,
                project_id=project_id,
                include_all_agents=True,
                prefer_categories=prefer,
            )
            trimmed = []
            for m in related_memories:
                if not isinstance(m, dict):
                    continue
                content = str(m.get("content") or "")
                if len(content) > LOCAL_SLM_MEMORY_CHARS:
                    content = content[: LOCAL_SLM_MEMORY_CHARS - 3] + "..."
                trimmed.append({**m, "content": content})
            related_memories = trimmed
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
        obs_cmd_cap = 400
        for tool_name, arguments, result in batch:
            ok = "ok" if getattr(result, "success", False) else "FAIL"
            if getattr(result, "duplicate_skip", False):
                ok = "skip"
            raw_out = str(getattr(result, "tool_output", "") or "")
            path = ""
            if isinstance(arguments, dict):
                path = str(arguments.get("path") or "")
            if tool_name == "read_file":
                from backend.services.tool_output_focus import read_file_observation_line

                lines.append(
                    f"- read_file: {ok} — {read_file_observation_line(path, raw_out)}"
                )
            else:
                cap = obs_cmd_cap if tool_name == "run_command" else 400
                if tool_name in (
                    "list_dir",
                    "grep",
                    "glob_file_search",
                    "search_code",
                    "semantic_search",
                ):
                    cap = max(cap, 400)
                out = raw_out.replace("\n", " ")[:cap]
                lines.append(f"- {tool_name}: {ok} — {out}")
            if path:
                files_touched.append(path)
            # Fold former per-tool system nudges into this one block.
            if getattr(result, "duplicate_skip", False):
                attempt = int(getattr(result, "duplicate_attempt", 0) or 0)
                if tool_name == "run_command":
                    hints.append(
                        "Command already succeeded this step — do not re-run; "
                        "run verification (lint/analyze/build) or update_board when AC are met."
                    )
                elif tool_name == "read_file":
                    hints.append(
                        "read_file duplicate skipped — use replayed content above or earlier messages; "
                        "call apply_patch next with verbatim old_text."
                    )
                else:
                    hints.append(
                        f"Already ran '{tool_name}' with identical args — use replayed output; do not repeat."
                    )
                if attempt >= 3:
                    hints.append(
                        "LOOP: You are retrying the same tool call repeatedly. "
                        "Stop this tool — use output already in context, edit files, or update_board."
                    )
                elif attempt == 2:
                    hints.append(
                        "Duplicate #2 — if you call the same tool again with these args, the step may hard-stop."
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
                task = find_task_by_id(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else None
                dep = _manifest_read_observation_hint(path_lower, task)
                if dep:
                    hints.append(f"read_file ok for '{path}'.{dep}")
                elif state.ACTIVE_SPRINT_AGENT == "Product Owner":
                    lane = get_task_lane(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else ""
                    if lane == "Needs PO":
                        hints.append(
                            f"read_file ok for '{path}' — content is in the tool message above; "
                            "do NOT read again. Reply with clarification JSON + update_board → In Progress."
                        )
                    else:
                        hints.append(
                            f"read_file ok for '{path}' — use output above; "
                            "call update_board or add_backlog_tasks next."
                        )
                else:
                    hints.append(
                        f"read_file ok for '{path}' — apply_patch next with verbatim old_text."
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
                elif getattr(result, "success", False) and not getattr(result, "duplicate_skip", False):
                    hints.append(
                        f"Command '{command[:80]}' succeeded (exit 0) — do not run it again. "
                        "Run the next verification step from acceptance criteria "
                        "(project lint/analyze/build) or update_board when all AC are satisfied."
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
        duplicate_attempt: int = 0,
    ) -> None:
        from backend.services.llm_context import prepare_tool_output_parts

        if duplicate_skip and tool_name == "read_file":
            path = str(arguments.get("path") or "")
            from backend.workspace.files import step_file_read_output_for_replay

            step_body = step_file_read_output_for_replay(path)
            if step_body and step_body.strip() not in str(tool_output or ""):
                raw = str(tool_output or "").strip()
                if raw.startswith("[") and "\n\n" in raw:
                    header = raw.split("\n\n", 1)[0].strip()
                    tool_output = f"{header}\n\n{step_body}"
                else:
                    tool_output = step_body

        parts = prepare_tool_output_parts(
            tool_name,
            tool_output,
            path=str(arguments.get("path") or "") if tool_name == "read_file" else None,
        )
        for idx, llm_output in enumerate(parts):
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": llm_output,
                }
            )
        if len(parts) > 1:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Tool '{tool_name}' returned {len(parts)} message part(s) above "
                        "(large output split like Cursor file chunks). Use all parts — do not re-run "
                        "the same tool to recover this listing."
                    ),
                }
            )
        llm_output = parts[0] if parts else str(tool_output or "")
        # When observation summaries are on, nudges live in === OBSERVATION === only.
        ws = get_workflow_settings()
        if ws.get("enableObservationSummaries", True):
            if success and tool_name == "read_file":
                path = str(arguments.get("path") or "?")
                if state.ACTIVE_SPRINT_AGENT == "Product Owner":
                    lane = get_task_lane(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else ""
                    task = find_task_by_id(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else None
                    if lane == "Needs PO":
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"read_file('{path}') is complete — full content in the tool message above; "
                                    "summary in === OBSERVATION === below. Do not read this path again. "
                                    "Produce clarification JSON, then update_board → In Progress."
                                ),
                            }
                        )
                    else:
                        from backend.services.tool_output_focus import is_dependency_manifest_path

                        if is_dependency_manifest_path(path.replace("\\", "/")):
                            messages.append(
                                {
                                    "role": "system",
                                    "content": _read_file_followup_system_message(path, task=task),
                                }
                            )
            if duplicate_skip and duplicate_attempt >= 2:
                from backend.services.duplicate_tool_policy import (
                    _suggested_next_after_duplicate,
                )

                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"=== DUPLICATE TOOL LOOP (call #{duplicate_attempt}) ===\n"
                            f"Do NOT invoke '{tool_name}' again with the same arguments. "
                            f"Full output is in the tool message(s) above and === OBSERVATION ===.\n"
                            f"NEXT: {_suggested_next_after_duplicate(tool_name, arguments)}"
                        ),
                    }
                )
            elif duplicate_skip and tool_name == "read_file":
                path = str(arguments.get("path") or "?")
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"read_file('{path}') duplicate skipped — full file content is in the "
                            "tool message above (same as the first read this step). "
                            "Do not read again; call apply_patch with verbatim old_text from that output."
                        ),
                    }
                )
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
            task = find_task_by_id(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else None
            messages.append(
                {
                    "role": "system",
                    "content": _read_file_followup_system_message(path, task=task),
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
            elif success and exit_code == 0 and not diagnostics:
                command = str(arguments.get("command") or "")
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Command succeeded: {command[:120]}. Do not run the same command again. "
                            "Proceed to verification (lint/analyze/build) for remaining acceptance criteria, "
                            "or update_board when the card is complete."
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

    def _num_ctx_ceiling(self) -> int:
        from backend.services.prompt_budget import resolve_ollama_num_ctx

        return resolve_ollama_num_ctx(self.role)

    def _effective_num_ctx(self) -> int:
        from backend.services.prompt_budget import initial_ollama_num_ctx

        ws = get_workflow_settings()
        if not ws.get("ollamaNumCtxAdaptive"):
            return self._num_ctx_ceiling()
        if self._step_num_ctx is None:
            self._step_num_ctx = initial_ollama_num_ctx(self.role)
        return min(self._num_ctx_ceiling(), self._step_num_ctx)

    def _bump_num_ctx_on_overflow(self) -> bool:
        from backend.services.prompt_budget import bump_ollama_num_ctx

        ws = get_workflow_settings()
        if not ws.get("ollamaNumCtxAdaptive"):
            return False
        ceiling = self._num_ctx_ceiling()
        current = self._effective_num_ctx()
        try:
            step = int(ws.get("ollamaNumCtxAdaptiveStep") or 8192)
        except (TypeError, ValueError):
            step = 8192
        nxt = bump_ollama_num_ctx(current, ceiling, step=step)
        if nxt is None:
            return False
        self._step_num_ctx = nxt
        return True

    def _chat_options(self) -> Dict[str, Any]:
        ws = get_workflow_settings()
        opts: Dict[str, Any] = {
            "temperature": 0.1,
            "num_ctx": self._effective_num_ctx(),
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
        num_ctx = self._effective_num_ctx()
        ceiling = self._num_ctx_ceiling()
        extra = ""
        if get_workflow_settings().get("ollamaNumCtxAdaptive") and num_ctx >= ceiling:
            extra = " Adaptive context is already at the configured ceiling."
        return (
            f"Request exceeded Ollama context (num_ctx={num_ctx}, ceiling={ceiling}).{extra} "
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
                while True:
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
                        if self._bump_num_ctx_on_overflow():
                            add_system_log(
                                self.role,
                                "info",
                                f"Context overflow — increasing num_ctx to {self._effective_num_ctx()} and retrying",
                            )
                            continue
                        overflow_msg = self._context_overflow_message()
                        add_system_log(self.role, "error", overflow_msg)
                        self._last_chat_error = err
                        self._last_chat_error_type = err_type
                        return None
                    break
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

    def _maybe_stop_after_dev_phase_nudge(
        self, tools_used: set[str]
    ) -> Optional[Tuple[str, str]]:
        """After explore nudge, stop if the model still did not write."""
        phase_graph = getattr(self, "_dev_phase_graph", None)
        if phase_graph is None:
            return None
        action = phase_graph.after_llm_turn_without_write()
        if not action.stop_reason:
            return None
        stop_msg = action.stop_message or action.stop_reason
        add_system_log(self.role, "warning", stop_msg)
        from backend.services.step_diagnostics import log_event

        log_event(action.stop_reason, stop_msg)
        self._log_step_exit(stop_msg, "warning")
        finish_run(status="failed", error=stop_msg)
        return action.stop_reason, stop_msg

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
        dev_phase: Optional[str] = None,
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
        phase_graph = getattr(self, "_dev_phase_graph", None)
        phase_label = dev_phase
        phase_snap: Optional[Dict[str, Any]] = None
        if phase_graph is not None:
            phase_snap = phase_graph.snapshot()
            if phase_label is None:
                phase_label = phase_snap.get("label") or phase_graph.label()
        update_kwargs: Dict[str, Any] = {
            "intent": intent,
            "card_progress": card,
        }
        if phase_label is not None:
            update_kwargs["dev_phase"] = phase_label
        if phase_snap is not None:
            update_kwargs["dev_phase_graph"] = phase_snap
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

        def _duplicate_success_key() -> Tuple[str, str]:
            if tool_name == "run_command" and isinstance(arguments, dict):
                from backend.services.duplicate_tool_policy import normalize_run_command_for_duplicate

                cmd = normalize_run_command_for_duplicate(str(arguments.get("command") or ""))
                return (tool_name, json.dumps({"command": cmd}, sort_keys=True))
            return key

        dup_key = _duplicate_success_key()

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
                    successful_tool_keys.append(dup_key)
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
            same_success = successful_tool_keys.count(dup_key)

        from backend.services.duplicate_tool_policy import (
            duplicate_in_step_hard_stop_applies,
            duplicate_in_step_soft_skip_applies,
        )

        def _read_file_dup_skip_ok() -> bool:
            if tool_name != "read_file":
                return True
            from backend.workspace.files import read_file_in_step_duplicate_skip_allowed

            return read_file_in_step_duplicate_skip_allowed(arguments)

        if (
            _read_file_dup_skip_ok()
            and duplicate_in_step_hard_stop_applies(tool_name)
            and same_success >= SAME_ARGS_SUCCESS_LIMIT - 1
        ):
            live_task = find_task_by_id(task_id) if task_id else None
            from backend.services.duplicate_tool_policy import duplicate_loop_should_hard_stop

            if duplicate_loop_should_hard_stop(same_success, limit=SAME_ARGS_SUCCESS_LIMIT):
                cmd_hint = ""
                if tool_name == "run_command" and isinstance(arguments, dict):
                    cmd_hint = str(arguments.get("command") or "")[:80]
                stop_msg = _duplicate_loop_stop_message(tool_name, arguments, same_success)
                self._log_step_exit(stop_msg, "warning")
                self._publish_work_progress(
                    task_id=task_id,
                    intent=f"Stopped duplicate loop {tool_name}"
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

            replay_pair = _resolve_in_step_duplicate_replay(
                tool_name, arguments, live_task, same_success
            )
            if replay_pair:
                tool_output, success = replay_pair
                skip_intent = f"Replayed duplicate {tool_name} (hard-stop avoided)"
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
                    successful_tool_keys.append(dup_key)
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
                setattr(result, "duplicate_skip", True)
                setattr(result, "duplicate_attempt", same_success + 1)
                return tool_name, arguments, result, None
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

        if (
            _read_file_dup_skip_ok()
            and duplicate_in_step_soft_skip_applies(tool_name)
            and same_success >= 1
        ):
            live_task = find_task_by_id(task_id) if task_id else None
            from backend.services.duplicate_tool_policy import duplicate_loop_should_hard_stop

            if duplicate_loop_should_hard_stop(same_success, limit=SAME_ARGS_SUCCESS_LIMIT):
                stop_msg = _duplicate_loop_stop_message(tool_name, arguments, same_success)
                self._log_step_exit(stop_msg, "warning")
                finish_run(status="failed", error=stop_msg)
                _track_fingerprint(block=True)
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

            replay_pair = _resolve_in_step_duplicate_replay(
                tool_name, arguments, live_task, same_success
            )
            if replay_pair:
                tool_output, success = replay_pair
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
                    successful_tool_keys.append(dup_key)
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
                setattr(result, "duplicate_skip", True)
                setattr(result, "duplicate_attempt", same_success + 1)
                return tool_name, arguments, result, None
            add_system_log(
                self.role,
                "info",
                f"No prior output for duplicate '{tool_name}' — executing tool",
            )

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
                successful_tool_keys.append(dup_key)
                if tool_name in ("apply_patch", "write_file"):
                    from backend.services.duplicate_tool_policy import (
                        purge_read_file_success_keys_for_path,
                    )

                    path_key = str(arguments.get("path") or "")
                    if path_key:
                        purge_read_file_success_keys_for_path(successful_tool_keys, path_key)
            _track_fingerprint()

        if not result.success and not result.pending_approval:
            with _FAILURE_LOCK:
                total_failures[0] += 1
                failed_tool_keys.append(key)
                same_count = failed_tool_keys.count(key)
                fail_total = total_failures[0]
            if tool_name in ("apply_patch", "write_file"):
                path_key = str(arguments.get("path") or "")
                if path_key:
                    from backend.workspace.files import invalidate_read_file_tracking_for_path
                    from backend.services.duplicate_tool_policy import (
                        purge_read_file_success_keys_for_path,
                    )

                    invalidate_read_file_tracking_for_path(path_key)
                    purge_read_file_success_keys_for_path(successful_tool_keys, path_key)
            _track_fingerprint()
            if same_count >= SAME_ARGS_FAILURE_LIMIT or (
                tool_name in PATH_TOOL_NAMES
                and same_count >= PATH_TOOL_SAME_ARGS_FAILURE_LIMIT
                and (
                    "invalid path" in (result.tool_output or "").lower()
                    or "do not retry" in (result.tool_output or "").lower()
                    or "not found" in (result.tool_output or "").lower()
                    or "stop calling" in (result.tool_output or "").lower()
                )
            ):
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
                duplicate_attempt=int(getattr(result, "duplicate_attempt", 0) or 0),
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
        from backend.services.step_recap import append_step_recap_after_batch_if_enabled

        append_step_recap_after_batch_if_enabled(
            messages,
            agent_role=self.role,
            task_id=task_id,
            batch=batch,
            tools_used_step=tools_used,
            successful_tool_keys=successful_tool_keys,
            iteration=iteration,
            max_iterations=max_iterations,
        )
        write_attempted = any(
            results_by_id[id(call)][0] in ("write_file", "apply_patch") for call in all_calls
        )
        write_success = any(
            results_by_id[id(call)][0] in ("write_file", "apply_patch")
            and results_by_id[id(call)][2].success
            for call in all_calls
        )
        if write_attempted and not write_success:
            from backend.services.llm_context import maybe_rewind_after_failed_writes

            rewinds_used = int(getattr(self, "_context_rewinds", 0) or 0)
            if rewinds_used < 2:
                removed = maybe_rewind_after_failed_writes(
                    messages,
                    write_attempted=True,
                    write_succeeded=False,
                )
                if removed:
                    self._context_rewinds = rewinds_used + 1
                    add_system_log(
                        self.role,
                        "info",
                        f"Context rewind after failed write — removed {removed} message(s) "
                        f"(rewind {self._context_rewinds}/2)",
                    )
                    from backend.services.step_diagnostics import log_event

                    log_event("context_rewind", f"removed={removed}")
        command_success = any(
            results_by_id[id(call)][0] == "run_command"
            and results_by_id[id(call)][2].success
            and not getattr(results_by_id[id(call)][2], "duplicate_skip", False)
            for call in all_calls
        )
        if write_success or command_success:
            self._inject_ac_progress_after_write(messages, task_id)
        if command_success and task_id:
            from backend.services.ac_command_match import apply_run_command_ac_ticks

            for call in all_calls:
                if not hasattr(call, "function"):
                    continue
                res = results_by_id.get(id(call))
                if not res or res[0] != "run_command" or not res[2].success:
                    continue
                if getattr(res[2], "duplicate_skip", False):
                    continue
                args = normalize_tool_arguments(call.function.arguments)
                cmd = str(args.get("command") or "")
                if cmd:
                    apply_run_command_ac_ticks(task_id, cmd, success=True)

        phase_graph = getattr(self, "_dev_phase_graph", None)
        if phase_graph is not None:
            batch_pairs = [
                (results_by_id[id(call)][0], bool(results_by_id[id(call)][2].success))
                for call in all_calls
            ]
            phase_action = phase_graph.record_batch(batch_pairs)
            from backend.services.step_diagnostics import log_event

            log_event("dev_phase", phase_graph.label())
            try:
                from backend.services.llm_decision_trace import (
                    build_decision_trace,
                    decision_trace_enabled,
                )
                from backend.services.llm_debug_log import amend_llm_log_entry

                if decision_trace_enabled() and task_id:
                    detail = phase_graph.label()
                    if phase_action.nudge:
                        detail += " · explore nudge"
                    if phase_action.stop_reason:
                        detail += f" · stop={phase_action.stop_reason}"
                    amend_llm_log_entry(
                        task_id,
                        iteration,
                        decision_trace=build_decision_trace(
                            outcome="dev_phase",
                            detail=detail,
                        ),
                    )
            except Exception:
                pass
            if phase_action.nudge:
                messages.append({"role": "system", "content": phase_action.nudge})
                add_system_log(self.role, "warning", "Dev explore budget — nudged model to apply_patch")
            self._publish_work_progress(
                task_id=task_id,
                intent=phase_graph.label(),
                status=phase_graph.label(),
                iteration=iteration,
                max_iterations=max_iterations,
                run_status="tool_executing",
                publish_activity_event=True,
                dev_phase=phase_graph.label(),
            )
            if phase_action.stop_reason:
                stop_msg = phase_action.stop_message or phase_action.stop_reason
                add_system_log(self.role, "warning", stop_msg)
                log_event(phase_action.stop_reason, stop_msg)
                self._log_step_exit(stop_msg, "warning")
                finish_run(status="failed", error=stop_msg)
                return stop_msg

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
        self._step_num_ctx = None
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
                from backend.services.duplicate_tool_policy import filter_tool_keys_for_in_step_seed

                successful_tool_keys.extend(filter_tool_keys_for_in_step_seed(seeded_success))
                failed_tool_keys.extend(seeded_fail)
            start_run(task_id, self.role, max_iterations=max_iterations)

        from backend.services.step_diagnostics import (
            build_live_intent,
            log_event,
            set_llm_iterations_max,
        )

        set_llm_iterations_max(max_iterations)
        pending_lesson: Optional[Tuple[str, set, str]] = None
        self._consecutive_echo_count = 0
        self._context_rewinds = 0
        self._dev_phase_graph = None
        from backend.services.dev_phase_graph import DevPhaseGraph

        lane_for_phase = get_task_lane(task_id) if task_id else None
        if DevPhaseGraph.applies_to(role=self.role, lane=lane_for_phase):
            self._dev_phase_graph = DevPhaseGraph.from_settings()

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
                phase_graph = getattr(self, "_dev_phase_graph", None)
                if phase_graph is not None:
                    intent = f"{phase_graph.label()} · {intent}"
                bundle_name = _apply_rotation_for_iteration(iteration)
                from backend.services.llm_context import maybe_inject_unchanged_prompt_progress

                if iteration == 1 and task_id:
                    from backend.services.step_recap import append_step_goal_anchor_if_enabled

                    append_step_goal_anchor_if_enabled(
                        messages,
                        agent_role=self.role,
                        task_id=task_id,
                    )
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
                    from backend.services.llm_decision_trace import (
                        build_decision_trace as _build_dt,
                        decision_trace_enabled as _dt_on,
                    )
                    from backend.services.llm_debug_log import amend_llm_log_entry as _amend_llm

                    if _dt_on():
                        _amend_llm(
                            task_id,
                            iteration,
                            decision_trace=_build_dt(
                                outcome="recovered_from_markdown",
                                detail=f"Parsed tool calls from assistant text: {', '.join(recovered_tool_names)}",
                            ),
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
                from backend.services.llm_decision_trace import (
                    build_decision_trace,
                    decision_trace_enabled,
                )
                from backend.services.llm_debug_log import amend_llm_log_entry
                from backend.services.llm_echo_guard import (
                    ECHO_REJECTION_MESSAGE,
                    detect_tool_output_echo,
                )

                if message.tool_calls:
                    self._consecutive_echo_count = 0
                    if decision_trace_enabled():
                        amend_llm_log_entry(
                            task_id,
                            iteration,
                            decision_trace=build_decision_trace(
                                outcome="tool_calls",
                                detail=f"Model invoked: {', '.join(tool_call_names)}",
                                tools_considered=list(self.registry.tool_names()),
                            ),
                        )
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
                        elif "explore tool budget" in low:
                            reason = "explore_budget_exhausted"
                        elif "patch tool budget" in low:
                            reason = "patch_budget_exhausted"
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
                echo_hit = detect_tool_output_echo(content, messages) if content else None
                if echo_hit and echo_hit.is_echo:
                    self._consecutive_echo_count = int(getattr(self, "_consecutive_echo_count", 0)) + 1
                    ws_echo = get_workflow_settings()
                    stop_after = max(1, int(ws_echo.get("toolOutputEchoStopAfter") or 2))
                    detail = echo_hit.reason or "Assistant repeated prior tool output"
                    add_system_log(
                        self.role,
                        "warning",
                        f"Tool output echo ({self._consecutive_echo_count}/{stop_after}): {detail}",
                    )
                    log_event("tool_output_echo", detail[:240])
                    if decision_trace_enabled():
                        amend_llm_log_entry(
                            task_id,
                            iteration,
                            decision_trace=build_decision_trace(
                                outcome="tool_output_echo",
                                detail=detail,
                                rejection="echo_of_tool_output",
                            ),
                            echo_detected=True,
                        )
                    if self._consecutive_echo_count >= stop_after:
                        stop_msg = (
                            f"Stopped: model repeated tool output {self._consecutive_echo_count} times "
                            "instead of calling edit tools."
                        )
                        add_system_log(self.role, "warning", stop_msg)
                        self._log_step_exit(stop_msg, "warning")
                        log_event("tool_output_echo_stop", stop_msg)
                        finish_run(status="failed", error=stop_msg)
                        pending_lesson = ("tool_output_echo", set(tools_used), stop_msg)
                        return stop_msg
                    messages.append({"role": "system", "content": ECHO_REJECTION_MESSAGE})
                    phase_stop = self._maybe_stop_after_dev_phase_nudge(tools_used)
                    if phase_stop:
                        pending_lesson = (
                            phase_stop[0],
                            set(tools_used),
                            phase_stop[1],
                        )
                        return phase_stop[1]
                    continue

                if content:
                    self._consecutive_echo_count = 0

                if content and _po_step_should_reject_text_only(content, tools_used, task_id):
                    if task_id and is_task_done(task_id) and not state.ALLOW_DONE_RETRY:
                        stop_msg = "Stopped: task already Done"
                        add_system_log(self.role, "info", stop_msg)
                        self._log_step_exit(stop_msg, "info")
                        finish_run(status="completed")
                        return stop_msg
                    if iteration >= max_iterations:
                        add_system_log(
                            self.role,
                            "warning",
                            "PO returned idle/onboarding text after exploration tools — max iterations.",
                        )
                        finish_run(status="failed", error=content[:300])
                        return content or "Task completed."
                    messages.append({"role": "assistant", "content": content})
                    add_system_log(
                        self.role,
                        "warning",
                        "PO idle or non-action text rejected — tool results are already in context; "
                        f"continuing to iter {min(iteration + 1, max_iterations)}/{max_iterations}.",
                    )
                    log_event("po_idle_rejected", content[:200])
                    messages.append(
                        {
                            "role": "system",
                            "content": _po_rejection_system_message(content, task_id),
                        }
                    )
                    continue

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
                    if decision_trace_enabled():
                        amend_llm_log_entry(
                            task_id,
                            iteration,
                            decision_trace=build_decision_trace(
                                outcome="text_rejected"
                                if reject_label == "text-only"
                                else "plan_rejected",
                                detail=(
                                    f"Rejected {reject_label} assistant text — "
                                    f"continuing to iter {next_iter}/{max_iterations}"
                                ),
                                rejection="text_only"
                                if reject_label == "text-only"
                                else "plan_only",
                            ),
                        )
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
                    phase_stop = self._maybe_stop_after_dev_phase_nudge(tools_used)
                    if phase_stop:
                        pending_lesson = (
                            phase_stop[0],
                            set(tools_used),
                            phase_stop[1],
                        )
                        return phase_stop[1]
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
