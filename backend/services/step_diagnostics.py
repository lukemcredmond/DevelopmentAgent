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
_WRITE_TOOLS = frozenset({"write_file", "apply_patch"})
_PATH_TOOLS = frozenset({"read_file", "list_dir", "glob_file_search", "grep"})
_FILE_SUMMARY_RE = re.compile(r"^([^\s(]+)")
_BOARD_LANE_RE = re.compile(r"→\s*(.+)$")
_TOOL_SUMMARY_OK = 300
_TOOL_SUMMARY_FAIL = 500


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_task_slug(task_id: str) -> str:
    return re.sub(r"[^\w\-]", "_", task_id)[:40]


def _first_error_line(summary: str, limit: int) -> str:
    text = str(summary or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip() or text
    return line[:limit]


def _lane_from_board_summary(summary: str) -> Optional[str]:
    match = _BOARD_LANE_RE.search(str(summary or ""))
    if not match:
        return None
    lane = match.group(1).strip()
    return lane or None


def classify_tool_failure(name: str, summary: str) -> Optional[str]:
    text = (summary or "").lower()
    if name == "apply_patch":
        if any(k in text for k in ("mismatch", "old_text", "not found", "context", "fuzzy", "does not match")):
            return "patch_mismatch"
        if "noop" in text or "0-char replace" in text:
            return "patch_noop"
        return "patch_failed"
    if name == "run_command":
        return "command_nonzero"
    if name in _PATH_TOOLS and any(
        k in text for k in ("not found", "no such", "missing", "does not exist")
    ):
        return "path_missing"
    if name == "update_board":
        return "board_blocked"
    return None


def _write_tools_succeeded(tools_log: Optional[List[Dict[str, Any]]] = None) -> bool:
    entries = tools_log
    if entries is None:
        trace = get_active_trace()
        entries = trace.tools_log if trace else []
    for entry in entries:
        if str(entry.get("toolName") or "") in _WRITE_TOOLS and entry.get("success"):
            return True
    return False


def _is_lint_recovery_message(agent_result: Optional[str]) -> bool:
    if not agent_result:
        return False
    lower = str(agent_result).lower()
    return (
        "staying in progress" in lower
        and ("lint/tool" in lower or "not moving to needs user" in lower)
    )


def _is_dev_precheck_skip_message(agent_result: Optional[str]) -> bool:
    if not agent_result:
        return False
    lower = str(agent_result).lower()
    return (
        "parking instead of another generate" in lower
        or "forced patch retry queued" in lower
        or "skipping another developer rewrite" in lower
        or "lint stall" in lower
    )


def _time_to_first_write_ms(trace: "StepDiagnosticsTracker") -> Optional[int]:
    started = _parse_ts(trace.started_at)
    if not started:
        return None
    for entry in trace.tools_log:
        name = str(entry.get("toolName") or "")
        if name not in _WRITE_TOOLS or not entry.get("success"):
            continue
        ts = _parse_ts(str(entry.get("timestamp") or ""))
        if ts:
            return max(0, int((ts - started).total_seconds() * 1000))
    return None


def _parse_ts(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _infer_primary_bottleneck(
    trace: "StepDiagnosticsTracker",
    *,
    exit_reason: Optional[str] = None,
) -> str:
    kinds = {str(e.get("kind") or "") for e in trace.events if isinstance(e, dict)}
    if "backup_model_switched" in kinds:
        return "backup_model_switch"
    if "tool_calls_recovered_from_content" in kinds:
        return "markdown_tool_recovery"
    if trace.runaway_generation_aborted or "runaway_generation_aborted" in kinds:
        return "runaway_generation"
    if str(exit_reason or "") == "phase_cycle_cap":
        return "visit_cap_latch"
    patch_fails = sum(
        1
        for e in trace.tools_log
        if isinstance(e, dict)
        and str(e.get("toolName") or "") == "apply_patch"
        and not e.get("success")
    )
    if patch_fails >= 2:
        return "patch_loop"
    if trace.text_rejections >= 2:
        return "text_rejection_loop"
    return "none"


def build_performance_summary(
    trace: "StepDiagnosticsTracker",
    *,
    duration_ms: int,
    writes_succeeded: int,
    native_rate: float,
    exit_reason: Optional[str] = None,
) -> Dict[str, Any]:
    events = trace.events or []
    markdown_recovery = any(
        isinstance(e, dict) and e.get("kind") == "tool_calls_recovered_from_content"
        for e in events
    )
    backup_switched = any(
        isinstance(e, dict) and e.get("kind") == "backup_model_switched"
        for e in events
    )
    backup_skipped = any(
        isinstance(e, dict) and e.get("kind") == "backup_model_skipped_recovery_ok"
        for e in events
    )
    return {
        "timeToFirstWriteMs": _time_to_first_write_ms(trace),
        "markdownRecovery": markdown_recovery,
        "backupSwitched": backup_switched,
        "backupSkippedRecoveryOk": backup_skipped,
        "primaryBottleneck": _infer_primary_bottleneck(trace, exit_reason=exit_reason),
        "fastSuccess": bool(
            writes_succeeded > 0 and native_rate >= 0.5 and duration_ms < 120_000
        ),
    }


def _apply_patch_failed_after_write(tools_log: Optional[List[Dict[str, Any]]] = None) -> bool:
    entries = tools_log
    if entries is None:
        trace = get_active_trace()
        entries = trace.tools_log if trace else []
    wrote = False
    patch_failed = False
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("toolName") or "")
        if name in _WRITE_TOOLS and entry.get("success"):
            wrote = True
        if name == "apply_patch" and not entry.get("success"):
            patch_failed = True
    return wrote and patch_failed


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
        self.identical_text_reject_count = 0
        self.forced_tool_mode = False
        self.forced_tool_mode_effective = False
        self.text_only_turns = 0
        self.native_tool_llm_calls = 0
        self.total_llm_calls_with_tools = 0
        self.native_tool_calls_before_recovery = 0
        self.recovery_only_tool_calls = 0
        self.prompt_tokens_at_first_call: Optional[int] = None
        self.runaway_generation_aborted = False
        self.llm_iterations_used = 0
        self.llm_iterations_max = 0
        self.tool_failures = 0
        self.last_event = "trace_started"
        self._live_logged = False
        self.sampling: Optional[Dict[str, Any]] = None
        self.phase_graph: Optional[Dict[str, Any]] = None
        self.po_json_applied: Optional[bool] = None
        self.po_num_predict_bumped = False
        self.lane_after_tool: Optional[str] = None
        self._last_checkpoint_monotonic: float = 0.0

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
        num_predict: Optional[int] = None,
        num_ctx: Optional[int] = None,
        done_reason: Optional[str] = None,
        prompt_eval_ms: Optional[int] = None,
        eval_ms: Optional[int] = None,
        truncated: Optional[bool] = None,
        attempt: Optional[int] = None,
        phase: Optional[str] = None,
        native_tool_calls: Optional[bool] = None,
        model_used: Optional[str] = None,
    ) -> None:
        self.llm_iterations_used = max(self.llm_iterations_used, iteration)
        self.last_event = f"ollama:iter{iteration}"
        cap = int(num_predict) if num_predict is not None else None
        eval_n = int(eval_tokens or 0)
        if truncated is None and cap and cap > 0:
            truncated = eval_n >= max(1, cap - 2)
        elif truncated is None and done_reason:
            truncated = str(done_reason).lower() in ("length", "max_tokens")
        entry: Dict[str, Any] = {
            "iteration": iteration,
            "durationMs": duration_ms,
            "toolCalls": tool_calls or [],
            "textChars": text_chars,
            "error": error,
            "promptTokens": int(prompt_tokens or 0),
            "evalTokens": eval_n,
            "totalTokens": int(total_tokens or (prompt_tokens or 0) + eval_n),
            "tokensReported": bool(tokens_reported),
        }
        if attempt is not None:
            entry["attempt"] = int(attempt)
        if phase:
            entry["phase"] = str(phase)
        if error_type:
            entry["errorType"] = error_type
        if cap is not None:
            entry["numPredict"] = cap
        if num_ctx is not None:
            entry["numCtx"] = int(num_ctx)
        if done_reason:
            entry["doneReason"] = str(done_reason)
        if self.prompt_tokens_at_first_call is None and int(prompt_tokens or 0) > 0:
            self.prompt_tokens_at_first_call = int(prompt_tokens or 0)
        if tool_calls:
            self.note_llm_call_with_tools()
            if native_tool_calls is True:
                self.note_native_tool_call()
            elif native_tool_calls is None and not any(
                str(e.get("kind") or "") == "tool_calls_recovered_from_content"
                for e in self.events[-5:]
            ):
                self.note_native_tool_call()
        if prompt_eval_ms is not None:
            entry["promptEvalMs"] = int(prompt_eval_ms)
        if eval_ms is not None:
            entry["evalMs"] = int(eval_ms)
        if truncated is not None:
            entry["truncated"] = bool(truncated)
        if model_used:
            entry["modelUsed"] = str(model_used)
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
        limit = _TOOL_SUMMARY_FAIL if not success and name in {"apply_patch", "run_command"} else _TOOL_SUMMARY_OK
        clipped = _first_error_line(summary, limit)
        entry: Dict[str, Any] = {
            "timestamp": _now_str(),
            "toolName": name,
            "success": success,
            "summary": clipped,
        }
        if duration_ms is not None:
            entry["durationMs"] = int(duration_ms)
        if not success:
            failure_class = classify_tool_failure(name, clipped)
            if failure_class:
                entry["failureClass"] = failure_class
        if name == "update_board" and success:
            lane = _lane_from_board_summary(clipped)
            if lane:
                self.lane_after_tool = lane
        self.tools_log.append(entry)
        if self.forced_tool_mode and success:
            self.note_forced_tool_mode_effective()
        self._flush_checkpoint()

    def note_forced_tool_mode_effective(self) -> None:
        self.forced_tool_mode_effective = True

    def note_native_tool_call(self) -> None:
        self.native_tool_llm_calls += 1

    def note_markdown_tool_recovery(
        self, *, native_before: bool, recovered_count: int
    ) -> None:
        if native_before:
            self.native_tool_calls_before_recovery += 1
        self.recovery_only_tool_calls += max(0, int(recovered_count or 0))

    def note_llm_call_with_tools(self) -> None:
        self.total_llm_calls_with_tools += 1

    def log_event(self, kind: str, message: str) -> None:
        if kind == "plan_rejected":
            self.plan_rejections += 1
        elif kind == "text_rejected":
            self.text_rejections += 1
            self.text_only_turns += 1
        elif kind == "identical_text_reject":
            self.identical_text_reject_count += 1
        elif kind == "forced_tool_mode":
            self.forced_tool_mode = True
        elif kind == "synthetic_read_fallback":
            self.note_forced_tool_mode_effective()
        elif kind == "runaway_generation_aborted":
            self.runaway_generation_aborted = True
        elif kind == "po_num_predict_bump":
            self.po_num_predict_bumped = True
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
            "max_iterations_after_writes": (
                "Agent wrote files then stopped after verify was already known "
                "(duplicate skip) or hit the iteration cap."
            ),
            "identical_write_loop": (
                "The same successful patch was applied repeatedly. Stop rewriting; "
                "verify the files or split the card."
            ),
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
            "text_rejection_loop": (
                "Model returned apology prose instead of tools. Try backup model, manual edit "
                "on the target file, or split the card."
            ),
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
        from backend.services.build_info import diagnostics_schema_version, get_app_build_info

        payload: Dict[str, Any] = {
            "diagnosticsSchemaVersion": diagnostics_schema_version(),
            "appBuild": get_app_build_info(),
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
            "identicalTextRejectCount": self.identical_text_reject_count,
            "forcedToolMode": self.forced_tool_mode,
            "forcedToolModeEffective": self.forced_tool_mode_effective,
            "textOnlyTurns": self.text_only_turns,
            "promptTokensAtFirstCall": self.prompt_tokens_at_first_call,
            "nativeToolCallRate": round(
                self.native_tool_llm_calls / max(1, len(self.ollama_calls)),
                3,
            )
            if self.ollama_calls
            else None,
            "nativeToolCallsBeforeRecovery": self.native_tool_calls_before_recovery,
            "recoveryOnlyToolCalls": self.recovery_only_tool_calls,
            "runawayGenerationAborted": self.runaway_generation_aborted,
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
        if self.sampling:
            payload["sampling"] = dict(self.sampling)
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
            and (
                self.llm_iterations_used > 0
                or len(self.tools_log) > 0
                or len(self.ollama_calls) > 0
            )
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
                ccs: Dict[str, Any] = {
                    "devStepCount": int(task.get("devStepCount") or 0),
                    "consecutiveBadExits": int(task.get("consecutiveBadExits") or 0),
                    "phaseCycleCapReached": bool(task.get("phaseCycleCapReached")),
                    "identicalPatchFailCount": int(task.get("identicalPatchFailCount") or 0),
                    "forcePatchNextDevStep": bool(task.get("forcePatchNextDevStep")),
                }
                graph = self.phase_graph
                if isinstance(graph, dict) and graph.get("phase"):
                    ccs["phaseGraph"] = {
                        "phase": graph.get("phase"),
                        "exploreCount": graph.get("exploreCount"),
                        "patchCount": graph.get("patchCount"),
                        "verifyCount": graph.get("verifyCount"),
                        "writeSucceeded": graph.get("writeSucceeded"),
                        "cycle": graph.get("cycle"),
                        "forcedPatch": graph.get("forcedPatch"),
                    }
                payload["cardCumulativeState"] = ccs
        except Exception:
            pass

        writes_attempted = 0
        writes_succeeded = 0
        write_paths: List[str] = []
        seen_paths: Set[str] = set()
        failure_classes: List[str] = []
        for entry in self.tools_log:
            name = str(entry.get("toolName") or "")
            if name in _WRITE_TOOLS:
                writes_attempted += 1
                if entry.get("success"):
                    writes_succeeded += 1
                    summary = str(entry.get("summary") or "").strip()
                    match = _FILE_SUMMARY_RE.match(summary)
                    path = match.group(1) if match else ""
                    if path and path != "?" and path not in seen_paths:
                        seen_paths.add(path)
                        write_paths.append(path)
            if entry.get("success") is False:
                cls = entry.get("failureClass") or classify_tool_failure(
                    name, str(entry.get("summary") or "")
                )
                if cls and cls not in failure_classes:
                    failure_classes.append(str(cls))
        payload["writesAttempted"] = writes_attempted
        payload["writesSucceeded"] = writes_succeeded
        payload["writePaths"] = write_paths[:12]
        lint_clean = bool(getattr(state, "FIX_VERIFY_LINT_CLEAN", False))
        try:
            from backend.agents.task_context import find_task_by_id as _find

            board_task = _find(self.task_id)
            if board_task is not None:
                lint_clean = lint_clean or bool(board_task.get("fixVerifyLintClean"))
        except Exception:
            pass
        payload["fixVerifyLintClean"] = lint_clean
        native_rate = (
            self.native_tool_llm_calls / max(1, len(self.ollama_calls))
            if self.ollama_calls
            else 0.0
        )
        perf = build_performance_summary(
            self,
            duration_ms=duration_ms,
            writes_succeeded=writes_succeeded,
            native_rate=native_rate,
            exit_reason=exit_reason if status == "complete" else None,
        )
        payload["performanceSummary"] = perf
        ttfw = perf.get("timeToFirstWriteMs")
        payload["cursorLikenessScore"] = bool(
            writes_succeeded > 0 and native_rate >= 0.5 and duration_ms < 180_000
        )
        payload["cursorLikenessScoreV2"] = bool(
            writes_succeeded > 0
            and duration_ms < 180_000
            and (
                native_rate >= 0.5
                or (
                    bool(perf.get("markdownRecovery"))
                    and ttfw is not None
                    and int(ttfw) < 45_000
                )
            )
        )
        if failure_classes:
            payload["toolFailureClasses"] = failure_classes
        if self.agent == "Product Owner":
            payload["poJsonApplied"] = bool(self.po_json_applied)
            payload["poNumPredictBumped"] = bool(self.po_num_predict_bumped)
            if self.lane_after_tool:
                payload["laneAfterTool"] = self.lane_after_tool

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
            if isinstance(last_step_outcome, dict):
                if last_step_outcome.get("parkAttempted"):
                    payload["parkAttempted"] = True
                    payload["parkSucceeded"] = bool(last_step_outcome.get("parkSucceeded"))
                block = str(last_step_outcome.get("parkBlockReason") or "").strip()
                if block:
                    payload["parkBlockReason"] = block
                kind = str(last_step_outcome.get("needsUserKind") or "").strip()
                if kind:
                    payload["needsUserKind"] = kind
        return payload

    def _flush_checkpoint(self, *, force: bool = False) -> None:
        import time

        now = time.monotonic()
        if not force and self._last_checkpoint_monotonic and (now - self._last_checkpoint_monotonic) < 60:
            if str(self.last_event or "").startswith("ollama_wait"):
                return
        self._last_checkpoint_monotonic = now
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
    num_predict: Optional[int] = None,
    num_ctx: Optional[int] = None,
    done_reason: Optional[str] = None,
    prompt_eval_ms: Optional[int] = None,
    eval_ms: Optional[int] = None,
    truncated: Optional[bool] = None,
    attempt: Optional[int] = None,
    phase: Optional[str] = None,
    native_tool_calls: Optional[bool] = None,
    model_used: Optional[str] = None,
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
            num_predict=num_predict,
            num_ctx=num_ctx,
            done_reason=done_reason,
            prompt_eval_ms=prompt_eval_ms,
            eval_ms=eval_ms,
            truncated=truncated,
            attempt=attempt,
            phase=phase,
            native_tool_calls=native_tool_calls,
            model_used=model_used,
        )
        from backend.services.sprint_session import touch_session

        touch_session(
            last_event=f"ollama:iter{iteration}",
            diagnostics_file=str(trace.file_path),
        )
        done_l = str(done_reason or "").lower()
        if done_l in ("length", "max_tokens"):
            log_event(
                "ctx_truncated",
                f"doneReason={done_reason} num_ctx={num_ctx} eval_count={eval_tokens}",
            )
        if error_type == "empty_generation_timeout":
            log_event(
                "empty_generation_timeout",
                f"elapsed_ms={duration_ms} eval_count={eval_tokens}",
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


def format_ollama_wait_event(
    *,
    iteration: int,
    max_iterations: int,
    model: str = "",
    elapsed_sec: int = 0,
    last_tool: Optional[str] = None,
) -> str:
    """lastEvent / diagnostics line while an Ollama call is in flight."""
    msg = (
        f"iter {int(iteration)}/{int(max_iterations)} "
        f"elapsed={int(elapsed_sec)}s model={str(model or '?')}"
    )
    tool = str(last_tool or "").strip()
    if tool:
        msg += f" last_tool={tool}"
    return msg


def format_console_ollama_wait(
    *,
    elapsed_sec: int,
    iteration: int,
    max_iterations: int,
    last_tool: Optional[str] = None,
) -> str:
    """Console line while an Ollama call is in flight (user-visible system log)."""
    msg = (
        f"Still waiting for Ollama — {int(elapsed_sec)}s, "
        f"iter {int(iteration)}/{int(max_iterations)}"
    )
    tool = str(last_tool or "").strip()
    if tool:
        msg += f", last_tool={tool}"
    return msg


def last_tool_name_from_active_trace() -> str:
    trace = get_active_trace()
    log = getattr(trace, "tools_log", None) or []
    if not log:
        return ""
    return str((log[-1] or {}).get("toolName") or "").strip()


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


def record_sampling_snapshot(sampling: Dict[str, Any]) -> None:
    trace = get_active_trace()
    if trace and sampling:
        snap = {k: v for k, v in sampling.items() if v is not None}
        if snap:
            trace.sampling = snap
            trace._flush_checkpoint()


def record_phase_graph(snapshot: Optional[Dict[str, Any]]) -> None:
    trace = get_active_trace()
    if trace and isinstance(snapshot, dict) and snapshot.get("phase"):
        trace.phase_graph = snapshot
        trace._flush_checkpoint()


def record_po_json_applied(applied: bool) -> None:
    trace = get_active_trace()
    if trace:
        trace.po_json_applied = bool(applied)
        trace._flush_checkpoint()


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
    if agent_result and str(agent_result).startswith("LLM_CALL_FAILED"):
        lower = str(agent_result).lower()
        if "empty generation" in lower:
            return "empty_generation_timeout"
        return "llm_call_failed"
    if agent_result and agent_result.startswith("Timed out:"):
        return "step_timeout"
    if agent_result and _is_lint_recovery_message(agent_result):
        return "lint_stay_in_progress"
    if agent_result and _is_dev_precheck_skip_message(agent_result):
        return "dev_precheck_skip"
    if agent_result and agent_result.startswith("Stopped:"):
        lower = agent_result.lower()
        if "text rejection loop" in lower:
            return "text_rejection_loop"
        if "phase cycle cap" in lower or "developer visit budget" in lower:
            return "phase_cycle_cap"
        if "generation truncated" in lower:
            return "po_generation_truncated"
        if "clarification incomplete" in lower:
            return "po_clarification_incomplete"
        if "identical arguments" in lower or "same arguments" in lower:
            return "duplicate_tool"
        if "explore tool budget" in lower:
            trace = get_active_trace()
            if trace and trace.tools_log:
                patch_attempted = any(
                    str(e.get("toolName") or "") == "apply_patch" for e in trace.tools_log
                )
                writes_attempted = sum(
                    1
                    for e in trace.tools_log
                    if str(e.get("toolName") or "") in _WRITE_TOOLS
                )
                if patch_attempted or writes_attempted > 0:
                    return "tool_failure_stop"
            return "explore_budget_exhausted"
        if "patch tool budget" in lower:
            return "patch_budget_exhausted"
        if "repeated tool output" in lower or "tool output echo" in lower:
            return "tool_output_echo"
        if "identical apply_patch" in lower or "same apply_patch" in lower:
            return "tool_failure_stop"
        if "identical write loop" in lower:
            return "identical_write_loop"
        if "files already written this step" in lower or "lint_after_write" in lower:
            return "max_iterations_after_writes"
        return "tool_failure_stop"
    wrote = False
    trace = get_active_trace()
    if trace:
        wrote = _write_tools_succeeded(trace.tools_log)
    if agent_result and agent_result.startswith("Max tool iterations"):
        if wrote:
            return "max_iterations_after_writes"
        return "max_iterations"
    if state.DEV_STEP_READ_ONLY_NO_EDITS:
        return "read_only_no_edits"
    if state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS:
        return "command_repeat_no_progress"
    if trace and trace.plan_rejections >= 2 and not wrote:
        return "plan_exhausted"
    if trace and _apply_patch_failed_after_write(trace.tools_log):
        try:
            from backend.agents.task_context import find_task_by_id
            from backend.services.needs_user_guard import stuck_is_tool_or_lint

            task = find_task_by_id(trace.task_id)
            if task and stuck_is_tool_or_lint(task):
                return "tool_failure_stop"
        except Exception:
            pass
    if wrote or (not trace and tools & _WRITE_TOOLS):
        return "completed_with_writes"
    if lane_before == "Needs PO":
        if lane_after != "Needs PO":
            return "po_clarified"
        return "po_clarification_incomplete"
    if lane_before == lane_after == "In Progress" and agent_result:
        if trace and str(getattr(trace, "agent", "") or "") == "System":
            if _is_lint_recovery_message(agent_result):
                return "lint_stay_in_progress"
            return "lint_stay_in_progress"
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
    outcome = state.LAST_STEP_OUTCOME if isinstance(state.LAST_STEP_OUTCOME, dict) else None
    ok = bool(outcome.get("ok")) if outcome else True
    if state.DEV_STEP_INTERRUPTED or state.SPRINT_CANCEL:
        ok = False
        if not outcome or str(outcome.get("exitReason") or "") != "interrupted":
            outcome = None
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
