"""Per-step sprint diagnostics — JSON files under ~/.allhands/diagnostics/."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from backend import state
from backend.config import diagnostics_dir
from backend.services.logs import add_system_log

MAX_FILES_PER_PROJECT = 50
TraceStatus = Literal["running", "complete"]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_task_slug(task_id: str) -> str:
    return re.sub(r"[^\w\-]", "_", task_id)[:40]


class StepDiagnosticsTracker:
    """Accumulates events for one sprint dev step and writes checkpoint JSON."""

    def __init__(
        self,
        *,
        task_id: str,
        task_title: str,
        agent: str,
        lane_before: str,
        file_path: Path,
    ) -> None:
        self.trace_id = uuid.uuid4().hex[:8].upper()
        self.task_id = task_id
        self.task_title = task_title
        self.agent = agent
        self.lane_before = lane_before
        self.file_path = file_path
        self.started_at = _now_str()
        self.started_monotonic = datetime.now()
        self.ollama_calls: List[Dict[str, Any]] = []
        self.tools_log: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.tools_used: Set[str] = set()
        self.plan_rejections = 0
        self.text_rejections = 0
        self.llm_iterations_used = 0
        self.llm_iterations_max = 0
        self.tool_failures = 0
        self.last_event = "trace_started"
        self._live_logged = False

    def log_ollama_call(
        self,
        iteration: int,
        *,
        duration_ms: int,
        tool_calls: Optional[List[str]] = None,
        text_chars: int = 0,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        prompt_tokens: int = 0,
        eval_tokens: int = 0,
        total_tokens: int = 0,
        tokens_reported: bool = False,
    ) -> None:
        self.llm_iterations_used = max(self.llm_iterations_used, iteration)
        self.last_event = f"ollama:iter{iteration}"
        entry: Dict[str, Any] = {
            "iteration": iteration,
            "durationMs": duration_ms,
            "toolCalls": tool_calls or [],
            "textChars": text_chars,
            "error": error,
            "promptTokens": int(prompt_tokens or 0),
            "evalTokens": int(eval_tokens or 0),
            "totalTokens": int(total_tokens or (prompt_tokens or 0) + (eval_tokens or 0)),
            "tokensReported": bool(tokens_reported),
        }
        if error_type:
            entry["errorType"] = error_type
        self.ollama_calls.append(entry)
        self._flush_checkpoint()
        # Live rollup onto the card
        try:
            from backend.services.agent_usage import record_ollama_call_usage

            record_ollama_call_usage(
                task_id=self.task_id,
                role=self.agent,
                duration_ms=duration_ms,
                prompt_tokens=int(prompt_tokens or 0),
                eval_tokens=int(eval_tokens or 0),
                tokens_reported=bool(tokens_reported),
            )
        except Exception:
            pass

    def set_llm_iterations_max(self, max_iterations: int) -> None:
        self.llm_iterations_max = max_iterations

    def log_tool(
        self, name: str, success: bool, summary: str, *, duration_ms: Optional[int] = None
    ) -> None:
        self.tools_used.add(name)
        if not success:
            self.tool_failures += 1
        self.last_event = f"tool:{name}"
        entry: Dict[str, Any] = {
            "timestamp": _now_str(),
            "toolName": name,
            "success": success,
            "summary": summary[:300],
        }
        if duration_ms is not None:
            entry["durationMs"] = int(duration_ms)
        self.tools_log.append(entry)
        self._flush_checkpoint()

    def log_event(self, kind: str, message: str) -> None:
        if kind == "plan_rejected":
            self.plan_rejections += 1
        elif kind == "text_rejected":
            self.text_rejections += 1
        self.last_event = f"{kind}:{message[:80]}"
        self.events.append(
            {
                "timestamp": _now_str(),
                "kind": kind,
                "message": message[:500],
            }
        )
        self._flush_checkpoint()

    def _build_hint(self, exit_reason: str) -> str:
        hints = {
            "phase_cycle_cap": (
                "Card reached its durable Developer visit limit. It is latched and must be "
                "split, clarified, or explicitly reset before Dev can run again."
            ),
            "read_only_no_edits": (
                "Model read files but never called apply_patch/write_file. "
                "Text/plan responses are not tools, backlog items, or memory — model must call apply_patch. "
                "Check Model tab iteration 2+ or attach this JSON."
            ),
            "max_iterations": "Agent hit max LLM iterations without finishing edits.",
            "step_timeout": (
                "Agent step exceeded maxAgentStepDurationSec — stopped to avoid an unbounded loop. "
                "Resume with Sprint step or chat."
            ),
            "duplicate_tool": "Same tool + identical args repeated — agent loop stop.",
            "tool_output_echo": (
                "Model repeated prior tool output in assistant text instead of calling edit tools — stopped to save GPU."
            ),
            "explore_budget_exhausted": (
                "Dev Explore budget reached without apply_patch/write_file. "
                "Split the card or narrow AC, then Run In Progress."
            ),
            "patch_budget_exhausted": (
                "Dev Patch budget reached without a successful write. "
                "Check apply_patch errors or Split the card."
            ),
            "tool_failure_stop": "Tool failures exceeded the step limit.",
            "tool_budget_exhausted": (
                "Step used its whole tool-call budget. Raise maxToolCallsPerStep or split the card."
            ),
            "ollama_fallback": "Ollama was unavailable during the step.",
            "completed_text_only": (
                "Agent returned text without write tools while still In Progress."
            ),
            "plan_exhausted": (
                "Multiple plan-only text responses were rejected; no edits written. "
                "Plan text is not executed — model must call apply_patch or write_file."
            ),
            "interrupted": "Step was cancelled or raised an exception before completing.",
            "po_clarification_incomplete": (
                "Product Owner did not apply clarification JSON or leave Needs PO. "
                "Valid JSON is applied automatically — do not restate it on the next step."
            ),
            "po_clarified": "PO clarification applied and the card left Needs PO.",
        }
        return hints.get(
            exit_reason,
            "See ollamaCalls and events in this file; attach when reporting issues.",
        )

    def _build_payload(
        self,
        *,
        status: TraceStatus,
        exit_reason: Optional[str] = None,
        lane_after: Optional[str] = None,
        ok: Optional[bool] = None,
        agent_result: Optional[str] = None,
        last_step_outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now()
        duration_ms = int((now - self.started_monotonic).total_seconds() * 1000)
        payload: Dict[str, Any] = {
            "traceId": self.trace_id,
            "projectId": state.CURRENT_PROJECT_ID,
            "taskId": self.task_id,
            "taskTitle": self.task_title,
            "agent": self.agent,
            "status": status,
            "lastEvent": self.last_event,
            "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
            "startedAt": self.started_at,
            "durationMs": duration_ms,
            "laneBefore": self.lane_before,
            "toolsUsed": sorted(self.tools_used),
            "toolFailures": self.tool_failures,
            "planRejections": self.plan_rejections,
            "textRejections": self.text_rejections,
            "llmIterations": {
                "used": self.llm_iterations_used,
                "max": self.llm_iterations_max,
            },
            "ollamaMsTotal": 0,
            "ollamaCallCount": len(self.ollama_calls),
            "promptTokensTotal": sum(int(c.get("promptTokens") or 0) for c in self.ollama_calls),
            "evalTokensTotal": sum(int(c.get("evalTokens") or 0) for c in self.ollama_calls),
            "totalTokens": sum(
                int(c.get("totalTokens") or 0)
                or (int(c.get("promptTokens") or 0) + int(c.get("evalTokens") or 0))
                for c in self.ollama_calls
            ),
            "tokensReported": any(bool(c.get("tokensReported")) for c in self.ollama_calls),
            "toolMsTotal": 0,  # filled below from toolsLog if duration present
            "ollamaCalls": self.ollama_calls,
            "toolsLog": self.tools_log,
            "events": self.events,
            "filePath": str(self.file_path),
        }
        ollama_sum = sum(int(c.get("durationMs") or 0) for c in self.ollama_calls)
        # Overlapping waits / retries can sum above wall clock — never report more LLM time than the step.
        payload["ollamaMsTotal"] = min(ollama_sum, duration_ms) if duration_ms > 0 else ollama_sum
        if duration_ms > 0 and ollama_sum > duration_ms:
            payload["ollamaMsCapped"] = True
        tool_ms = 0
        for entry in self.tools_log:
            if isinstance(entry.get("durationMs"), (int, float)):
                tool_ms += int(entry["durationMs"])
        payload["toolMsTotal"] = tool_ms
        if (
            isinstance(state.LAST_STEP_PROGRESS, dict)
            and str(state.LAST_STEP_PROGRESS.get("taskId") or "") == self.task_id
        ):
            payload["stepProgress"] = state.LAST_STEP_PROGRESS
        payload["currentStepActivity"] = {
            "iterationsUsed": self.llm_iterations_used,
            "iterationsMax": self.llm_iterations_max,
            "toolFailures": self.tool_failures,
            "planRejections": self.plan_rejections,
            "textRejections": self.text_rejections,
            "ollamaCallCount": len(self.ollama_calls),
            "toolCallCount": len(self.tools_log),
        }
        try:
            from backend.agents.task_context import find_task_by_id

            task = find_task_by_id(self.task_id)
            if task:
                payload["cardCumulativeState"] = {
                    "devStepCount": int(task.get("devStepCount") or 0),
                    "consecutiveBadExits": int(task.get("consecutiveBadExits") or 0),
                    "phaseCycleCapReached": bool(task.get("phaseCycleCapReached")),
                    "identicalPatchFailCount": int(task.get("identicalPatchFailCount") or 0),
                }
        except Exception:
            pass

        if status == "complete":
            payload.update(
                {
                    "endedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "exitReason": exit_reason,
                    "laneAfter": lane_after,
                    "ok": ok,
                    "agentResultSnippet": (agent_result or "")[:500],
                    "lastStepOutcome": last_step_outcome,
                    "hint": self._build_hint(exit_reason or ""),
                }
            )
        return payload

    def _flush_checkpoint(self) -> None:
        payload = self._build_payload(status="running")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        if not self._live_logged:
            self._live_logged = True
            add_system_log(
                "System",
                "info",
                f"Step diagnostics (live): {self.file_path}",
            )

    def finalize(
        self,
        *,
        exit_reason: str,
        lane_after: str,
        ok: bool,
        agent_result: Optional[str] = None,
        last_step_outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self._build_payload(
            status="complete",
            exit_reason=exit_reason,
            lane_after=lane_after,
            ok=ok,
            agent_result=agent_result,
            last_step_outcome=last_step_outcome,
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        try:
            from backend.services.agent_usage import record_step_usage_from_trace

            record_step_usage_from_trace(self)
        except Exception:
            pass
        _prune_old_files(self.file_path.parent)
        duration_ms = payload["durationMs"]
        tools_summary = ",".join(sorted(self.tools_used)) or "none"
        add_system_log(
            "System",
            "info",
            f"Step diagnostics: {self.file_path} (exit={exit_reason}, tools={tools_summary}, {duration_ms // 1000}s)",
        )
        return payload


def _prune_old_files(project_dir: Path) -> None:
    files = sorted(project_dir.glob("step-*.json"), key=lambda p: p.stat().st_mtime)
    while len(files) > MAX_FILES_PER_PROJECT:
        oldest = files.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def get_active_trace() -> Optional[StepDiagnosticsTracker]:
    return state.ACTIVE_STEP_DIAGNOSTICS


def get_active_trace_summary() -> Optional[Dict[str, Any]]:
    trace = get_active_trace()
    if not trace:
        return None
    return {
        "traceId": trace.trace_id,
        "filePath": str(trace.file_path),
        "status": "running",
        "taskId": trace.task_id,
        "taskTitle": trace.task_title,
        "lastEvent": trace.last_event,
        "updatedAt": _now_str(),
    }


def start_step_trace(
    task_id: str,
    task_title: str,
    agent: str,
    lane: str,
) -> StepDiagnosticsTracker:
    # Agent result is step-local; never let a prior card's stop text determine this exit.
    state.LAST_AGENT_STEP_RESULT = None
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    slug = _safe_task_slug(task_id)
    project_dir = diagnostics_dir(state.CURRENT_PROJECT_ID)
    file_path = project_dir / f"step-{slug}-{stamp}.json"
    tracker = StepDiagnosticsTracker(
        task_id=task_id,
        task_title=task_title,
        agent=agent,
        lane_before=lane,
        file_path=file_path,
    )
    state.ACTIVE_STEP_DIAGNOSTICS = tracker
    add_system_log(
        "System",
        "info",
        f"Step diagnostics trace {tracker.trace_id} started — {file_path}",
    )
    tracker._flush_checkpoint()
    from backend.services.sprint_session import touch_session

    touch_session(
        last_event="trace_started",
        diagnostics_file=str(tracker.file_path),
        force=True,
    )
    return tracker


def finalize_orphaned_diagnostics(
    *,
    task_id: str,
    diagnostics_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Finalize a diagnostics JSON file left in running state after app restart."""
    project_dir = diagnostics_dir(state.CURRENT_PROJECT_ID)
    target_path: Optional[Path] = None

    if diagnostics_path:
        candidate = Path(diagnostics_path)
        if candidate.is_file():
            target_path = candidate

    if target_path is None and task_id:
        matches: List[tuple[float, Path, Dict[str, Any]]] = []
        for file_path in project_dir.glob("step-*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("status") == "running" and data.get("taskId") == task_id:
                matches.append((file_path.stat().st_mtime, file_path, data))
        if matches:
            matches.sort(key=lambda item: item[0], reverse=True)
            target_path = matches[0][1]

    if target_path is None:
        return None

    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if data.get("status") != "running":
        return {
            "filePath": str(target_path),
            "lastEvent": data.get("lastEvent", ""),
        }

    now = datetime.now()
    duration_ms = int(data.get("durationMs", 0))
    started_at = data.get("startedAt")
    if isinstance(started_at, str):
        try:
            started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
            duration_ms = int((now - started).total_seconds() * 1000)
        except ValueError:
            pass

    data.update(
        {
            "status": "complete",
            "exitReason": "interrupted",
            "endedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": False,
            "laneAfter": data.get("laneBefore"),
            "hint": "App restarted during this step",
            "durationMs": duration_ms,
        }
    )
    with open(target_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)

    return {
        "filePath": str(target_path),
        "lastEvent": data.get("lastEvent", ""),
    }


def log_ollama_call(
    iteration: int,
    *,
    duration_ms: int,
    tool_calls: Optional[List[str]] = None,
    text_chars: int = 0,
    error: Optional[str] = None,
    error_type: Optional[str] = None,
    prompt_tokens: int = 0,
    eval_tokens: int = 0,
    total_tokens: int = 0,
    tokens_reported: bool = False,
) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_ollama_call(
            iteration,
            duration_ms=duration_ms,
            tool_calls=tool_calls,
            text_chars=text_chars,
            error=error,
            error_type=error_type,
            prompt_tokens=prompt_tokens,
            eval_tokens=eval_tokens,
            total_tokens=total_tokens,
            tokens_reported=tokens_reported,
        )
        from backend.services.sprint_session import touch_session

        touch_session(
            last_event=f"ollama:iter{iteration}",
            diagnostics_file=str(trace.file_path),
        )


def log_tool(
    name: str, success: bool, summary: str, *, duration_ms: Optional[int] = None
) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_tool(name, success, summary, duration_ms=duration_ms)
        from backend.services.sprint_session import touch_session

        touch_session(
            last_event=f"tool:{name}",
            diagnostics_file=str(trace.file_path),
        )


def log_event(kind: str, message: str) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_event(kind, message)
        from backend.services.sprint_session import touch_session

        touch_session(
            last_event=f"{kind}:{message[:80]}",
            diagnostics_file=str(trace.file_path),
        )


def set_llm_iterations_max(max_iterations: int) -> None:
    trace = get_active_trace()
    if trace:
        trace.set_llm_iterations_max(max_iterations)


_WRITE_TOOLS = frozenset({"write_file", "apply_patch"})
_FILE_SUMMARY_RE = re.compile(r"^([^\s(]+)")


def gates_remaining_for_lane(lane: Optional[str]) -> List[str]:
    """Lanes still ahead before Done (honest pipeline remaining)."""
    from backend.services.workflow_settings import get_workflow_settings

    settings = get_workflow_settings()
    order = ["In Progress"]
    if settings.get("requireCodeReview"):
        order.append("Code Review")
    order.extend(["QA", "Done"])
    current = (lane or "").strip()
    if current == "Done":
        return []
    if current not in order:
        # Needs PO / Needs User / Backlog etc. — full remaining implementation gates
        return [g for g in order if g != "In Progress"]
    idx = order.index(current)
    return order[idx + 1 :]


def files_written_this_step(tools_log: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Paths written via write_file / apply_patch in this step's tools_log."""
    trace = get_active_trace()
    entries = tools_log if tools_log is not None else (trace.tools_log if trace else [])
    paths: List[str] = []
    seen: set[str] = set()
    for entry in entries:
        name = str(entry.get("toolName") or "")
        if name not in _WRITE_TOOLS:
            continue
        if entry.get("success") is False:
            continue
        summary = str(entry.get("summary") or "").strip()
        match = _FILE_SUMMARY_RE.match(summary)
        path = match.group(1) if match else ""
        if path and path != "?" and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths[:12]


def build_card_work_snapshot(
    task: Optional[Dict[str, Any]] = None,
    *,
    task_id: Optional[str] = None,
    lane: Optional[str] = None,
    files_this_step: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Honest card-level remaining-work signals (no invented AC %)."""
    from backend.agents.task_context import find_task_by_id, get_task_lane, is_task_done, normalize_task

    if task is None and task_id:
        task = find_task_by_id(str(task_id))
    if task:
        normalize_task(task)
        task_id = str(task.get("id") or task_id or "")
    else:
        task = {}

    resolved_lane = lane or (get_task_lane(task_id) if task_id else None) or str(task.get("status") or "")
    subtask_ids = [str(s) for s in (task.get("subtaskIds") or [])]
    subtasks_done = sum(1 for sid in subtask_ids if is_task_done(sid))
    ac = task.get("acceptanceCriteria") or []
    ac_count = len(ac) if isinstance(ac, list) else 0
    stuck = int(task.get("stuckLoops") or 0)
    dev_steps = int(task.get("devStepCount") or 0)
    files = files_this_step if files_this_step is not None else files_written_this_step()

    work_items: List[Dict[str, Any]] = []
    if task:
        try:
            from backend.services.agent_work_items import refresh_agent_work_items

            work_items = refresh_agent_work_items(task)
        except Exception:
            raw = task.get("agentWorkItems") or []
            work_items = [x for x in raw if isinstance(x, dict)][:12]

    return {
        "subtasksDone": subtasks_done,
        "subtasksTotal": len(subtask_ids),
        "stepsOnCard": dev_steps,
        "stuckLoops": stuck,
        "poRoundTrips": int(task.get("poRoundTrips") or 0),
        "gatesRemaining": gates_remaining_for_lane(resolved_lane),
        "filesThisStep": files,
        "acCount": ac_count,
        "lane": resolved_lane,
        "agentWorkItems": work_items,
    }


def build_live_intent(
    *,
    phase: str,
    iteration: int = 0,
    max_iterations: int = 0,
    tool_name: Optional[str] = None,
    tool_summary: Optional[str] = None,
    reject_label: Optional[str] = None,
    elapsed_sec: Optional[int] = None,
    model: Optional[str] = None,
) -> str:
    """One-line what-the-agent-is-doing-now for UI."""
    iter_bit = f" — iter {iteration}/{max_iterations}" if max_iterations else ""
    if phase == "awaiting_ollama":
        model_bit = f" ({model})" if model else ""
        elapsed_bit = f" · {elapsed_sec}s" if elapsed_sec is not None and elapsed_sec > 0 else ""
        return (
            f"Waiting for model (Ollama){model_bit}{iter_bit}{elapsed_bit} "
            "— LLM call in flight, no tool running"
        )[:220]
    if phase == "thinking":
        if max_iterations:
            return f"Thinking (iter {iteration}/{max_iterations})"
        return "Thinking"
    if phase in ("plan_reject", "text_reject") or reject_label:
        label = reject_label or ("plan-only" if phase == "plan_reject" else "text-only")
        return f"Retrying after {label} — need apply_patch{iter_bit}"
    if phase == "tool" or tool_name:
        name = tool_name or "tool"
        detail = (tool_summary or "").strip()
        if detail and not detail.startswith(name):
            return f"Running {name}: {detail[:200]}"
        if detail:
            return f"Running {detail[:200]}"
        return f"Running {name}{iter_bit}"
    if phase == "completed":
        return "Step completed"
    if phase == "failed":
        return "Step failed"
    return (phase or "Working")[:160]


def build_step_progress(
    *,
    task_id: Optional[str],
    iterations_used: int,
    iterations_max: int,
    tools_used: Optional[Set[str]] = None,
    failed_tool_keys: Optional[List[Any]] = None,
    stuck_loop: bool = False,
    intent: Optional[str] = None,
    why_card_stayed: Optional[str] = None,
    suggested_action: Optional[str] = None,
    card_progress: Optional[Dict[str, Any]] = None,
    dev_phase_graph: Optional[Dict[str, Any]] = None,
    model_switches: int = 0,
) -> Dict[str, Any]:
    """Snapshot of what the agent did — used for max-iter Extend UX + card observability."""
    trace = get_active_trace()
    tools_ordered: List[str] = []
    last_tools: List[Dict[str, Any]] = []
    plan_rej = 0
    text_rej = 0
    duration_ms: Optional[int] = None
    last_tool_summary = ""

    if trace:
        plan_rej = trace.plan_rejections
        text_rej = trace.text_rejections
        duration_ms = int((datetime.now() - trace.started_monotonic).total_seconds() * 1000)
        for entry in trace.tools_log:
            name = str(entry.get("toolName") or "")
            if name and name not in tools_ordered:
                tools_ordered.append(name)
        last_tools = [
            {
                "toolName": e.get("toolName"),
                "success": e.get("success"),
                "summary": str(e.get("summary") or "")[:120],
            }
            for e in trace.tools_log[-5:]
        ]
        if trace.tools_log:
            last = trace.tools_log[-1]
            last_tool_summary = (
                f"{last.get('toolName')}: {str(last.get('summary') or '')[:160]}"
            )
        # Detect repeated same-args failures from tools_log names if keys not passed
        if not stuck_loop and failed_tool_keys:
            from collections import Counter

            counts = Counter(failed_tool_keys)
            stuck_loop = any(c >= 2 for c in counts.values())
        elif not stuck_loop and len(trace.tools_log) >= 3:
            recent = [e.get("toolName") for e in trace.tools_log[-3:] if e.get("success") is False]
            if len(recent) >= 3 and len(set(recent)) == 1:
                stuck_loop = True

    if tools_used:
        for name in sorted(tools_used):
            if name not in tools_ordered:
                tools_ordered.append(name)

    files_this_step = files_written_this_step()
    resolved_task_id = task_id or (trace.task_id if trace else None)
    snapshot = card_progress or build_card_work_snapshot(
        task_id=str(resolved_task_id) if resolved_task_id else None,
        files_this_step=files_this_step,
    )

    if not intent and last_tool_summary:
        intent = build_live_intent(phase="tool", tool_summary=last_tool_summary)
    elif not intent:
        intent = build_live_intent(
            phase="thinking",
            iteration=iterations_used,
            max_iterations=iterations_max,
        )

    phase_snap = dev_phase_graph
    if phase_snap is None:
        run = getattr(state, "ACTIVE_AGENT_RUN", None)
        if run is not None and getattr(run, "dev_phase_graph", None):
            phase_snap = dict(run.dev_phase_graph)

    llm_calls = len(trace.ollama_calls) if trace else int(iterations_used or 0)
    tool_calls = len(trace.tools_log) if trace else len(tools_ordered)
    failed_tools = int(trace.tool_failures) if trace else 0
    prompt_chars_approx = 0
    if trace and trace.ollama_calls:
        for call in trace.ollama_calls:
            prompt_chars_approx += int(call.get("textChars") or 0)
            # Prefer token-based estimate when available (~4 chars/token)
            pt = int(call.get("promptTokens") or 0)
            if pt:
                prompt_chars_approx = max(prompt_chars_approx, pt * 4)
    progress: Dict[str, Any] = {
        "taskId": resolved_task_id,
        "iterationsUsed": iterations_used,
        "iterationsMax": iterations_max,
        "toolsUsed": tools_ordered,
        "lastTools": last_tools,
        "planRejections": plan_rej,
        "textRejections": text_rej,
        "lastToolSummary": last_tool_summary,
        "stuckLoop": stuck_loop,
        "intent": intent,
        "cardProgress": snapshot,
        "filesThisStep": files_this_step,
        "llmCalls": llm_calls,
        "toolCalls": tool_calls,
        "failedTools": failed_tools,
        "promptCharsApprox": prompt_chars_approx,
        "modelSwitches": int(model_switches or 0),
    }
    if duration_ms is not None:
        progress["durationMs"] = duration_ms
    if why_card_stayed:
        progress["whyCardStayed"] = why_card_stayed
    if suggested_action:
        progress["suggestedAction"] = suggested_action
    if phase_snap:
        progress["devPhaseGraph"] = phase_snap
        if phase_snap.get("label"):
            progress["devPhase"] = phase_snap.get("label")
    return progress


def store_step_progress(progress: Dict[str, Any]) -> None:
    state.LAST_STEP_PROGRESS = progress
    task_id = progress.get("taskId")
    if not task_id:
        return
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(str(task_id))
    if task:
        normalize_task(task)
        task["lastStepProgress"] = progress


def _phase_graph_richness(snap: Optional[Dict[str, Any]]) -> tuple:
    """Sort key: prefer higher cycle, longer history, then terminal progress."""
    if not snap or not isinstance(snap, dict):
        return (0, 0, 0, 0)
    hist = snap.get("cycleHistory") or snap.get("cycle_history") or []
    hist_len = len(hist) if isinstance(hist, list) else 0
    cycle = int(snap.get("cycle") or 0)
    phase = str(snap.get("phase") or "").lower()
    phase_rank = {"done": 4, "stuck": 3, "verify": 2, "patch": 1, "explore": 0}.get(phase, 0)
    tools = (
        int(snap.get("exploreCount") or snap.get("explore_count") or 0)
        + int(snap.get("patchCount") or snap.get("patch_count") or 0)
        + int(snap.get("verifyCount") or snap.get("verify_count") or 0)
    )
    return (cycle, hist_len, phase_rank, tools)


def prefer_richer_phase_graph(
    incoming: Optional[Dict[str, Any]],
    existing: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Keep the graph with more cycle/history signal; never replace richer with poorer."""
    if incoming and not existing:
        return dict(incoming)
    if existing and not incoming:
        return dict(existing)
    if not incoming and not existing:
        return None
    if _phase_graph_richness(incoming) >= _phase_graph_richness(existing):
        return dict(incoming)  # type: ignore[arg-type]
    return dict(existing)  # type: ignore[arg-type]


def persist_step_progress_from_active_run(
    *,
    dev_phase_graph: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Merge current agent-run phase graph into task.lastStepProgress before finish_run clears the run.
    Prefer richer graphs so stale Cycle 1 blobs cannot wipe Cycle 2 history.
    """
    run = getattr(state, "ACTIVE_AGENT_RUN", None)
    task_id = getattr(run, "task_id", None) if run is not None else None
    phase_snap = dev_phase_graph
    if phase_snap is None and run is not None:
        raw = getattr(run, "dev_phase_graph", None)
        if isinstance(raw, dict) and raw.get("phase"):
            phase_snap = dict(raw)

    existing: Optional[Dict[str, Any]] = None
    if isinstance(getattr(state, "LAST_STEP_PROGRESS", None), dict):
        global_progress = state.LAST_STEP_PROGRESS
        if not task_id or str(global_progress.get("taskId") or "") == str(task_id):
            existing = global_progress
    if task_id:
        from backend.agents.task_context import find_task_by_id, normalize_task

        task = find_task_by_id(str(task_id))
        if task:
            normalize_task(task)
            lsp = task.get("lastStepProgress")
            if isinstance(lsp, dict):
                existing = lsp if existing is None else existing

    existing_graph = None
    if isinstance(existing, dict):
        eg = existing.get("devPhaseGraph") or existing.get("dev_phase_graph")
        if isinstance(eg, dict) and eg.get("phase"):
            existing_graph = eg

    chosen = prefer_richer_phase_graph(phase_snap, existing_graph)
    if chosen is None and existing is None:
        return None

    # Start from same-task progress only. Current-step counters never inherit prior runs.
    base = dict(existing) if isinstance(existing, dict) else {}
    iterations_used = 0
    iterations_max = 0
    if run is not None:
        iterations_used = int(getattr(run, "iteration", 0) or 0)
        iterations_max = int(getattr(run, "max_iterations", 0) or 0)
        task_id = task_id or getattr(run, "task_id", None)

    progress = build_step_progress(
        task_id=str(task_id) if task_id else None,
        iterations_used=iterations_used,
        iterations_max=iterations_max,
        tools_used=set(base.get("toolsUsed") or base.get("tools_used") or []) or None,
        stuck_loop=bool(base.get("stuckLoop") or base.get("stuck_loop")),
        intent=base.get("intent") or (getattr(run, "intent", None) if run else None),
        why_card_stayed=base.get("whyCardStayed") or base.get("why_card_stayed"),
        suggested_action=base.get("suggestedAction") or base.get("suggested_action"),
        card_progress=base.get("cardProgress") or base.get("card_progress"),
        dev_phase_graph=chosen,
    )
    # Preserve richer graph even if build_step_progress pulled a poorer run snap
    if chosen:
        progress["devPhaseGraph"] = chosen
        if chosen.get("label"):
            progress["devPhase"] = chosen.get("label")
    store_step_progress(progress)
    return progress


def derive_exit_reason(
    *,
    agent_result: Optional[str],
    tools_used: Optional[Set[str]],
    lane_before: str,
    lane_after: str,
) -> str:
    tools = tools_used or set()
    if state.DEV_STEP_INTERRUPTED or state.SPRINT_CANCEL:
        return "interrupted"
    if agent_result == "SIMULATION_FALLBACK":
        return "ollama_fallback"
    if agent_result and agent_result.startswith("Timed out:"):
        return "step_timeout"
    if agent_result and agent_result.startswith("Stopped:"):
        lower = agent_result.lower()
        if "phase cycle cap" in lower or "developer visit budget" in lower:
            return "phase_cycle_cap"
        if "identical arguments" in lower or "same arguments" in lower:
            return "duplicate_tool"
        if "explore tool budget" in lower:
            return "explore_budget_exhausted"
        if "patch tool budget" in lower:
            return "patch_budget_exhausted"
        if "repeated tool output" in lower or "tool output echo" in lower:
            return "tool_output_echo"
        if "identical apply_patch" in lower or "same apply_patch" in lower:
            return "tool_failure_stop"
        return "tool_failure_stop"
    if agent_result and agent_result.startswith("Max tool iterations"):
        return "max_iterations"
    if state.DEV_STEP_READ_ONLY_NO_EDITS:
        return "read_only_no_edits"
    if state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS:
        return "command_repeat_no_progress"
    trace = get_active_trace()
    if trace and trace.plan_rejections >= 2 and not (tools & {"write_file", "apply_patch"}):
        return "plan_exhausted"
    if tools & {"write_file", "apply_patch"}:
        return "completed_with_writes"
    if lane_before == "Needs PO":
        if lane_after != "Needs PO":
            return "po_clarified"
        return "po_clarification_incomplete"
    if lane_before == lane_after == "In Progress" and agent_result:
        return "completed_text_only"
    if lane_before != lane_after:
        return "lane_advanced"
    return "completed_text_only"


def finalize_active_step_trace(
    *,
    lane_after: str,
    agent_result: Optional[str] = None,
    tools_used: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    trace = get_active_trace()
    if not trace:
        return None
    outcome = state.LAST_STEP_OUTCOME
    ok = bool(outcome.get("ok")) if outcome else True
    if state.DEV_STEP_INTERRUPTED or state.SPRINT_CANCEL:
        ok = False
    exit_reason = derive_exit_reason(
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
        tools_used=tools_used or trace.tools_used,
        lane_before=trace.lane_before,
        lane_after=lane_after,
    )
    summary = trace.finalize(
        exit_reason=exit_reason,
        lane_after=lane_after,
        ok=ok,
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
        last_step_outcome=outcome,
    )
    state.LAST_STEP_DIAGNOSTICS = summary
    state.ACTIVE_STEP_DIAGNOSTICS = None
    return summary


def clear_active_step_trace() -> None:
    state.ACTIVE_STEP_DIAGNOSTICS = None
