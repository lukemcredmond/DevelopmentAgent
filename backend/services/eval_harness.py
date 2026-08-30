"""Offline eval harness: run golden coding tasks through the real sprint pipeline.

The harness exists so that model/prompt/budget changes can be measured instead of
guessed. It drives `run_sprint_step` against a disposable workspace, then scores the
result objectively (did the verify command pass, did the expected files change) and
pulls per-step economics out of the step diagnostics JSON that the pipeline already
writes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend import state
from backend.config import diagnostics_dir

DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "tasks"
DEFAULT_VERIFY_TIMEOUT_SEC = 120
DEFAULT_MAX_STEPS = 8

# Exit reasons that mean the loop ran out of road rather than finishing the work.
BUDGET_EXHAUSTED_EXITS = frozenset(
    {
        "max_iterations",
        "step_timeout",
        "explore_budget_exhausted",
        "patch_budget_exhausted",
        "duplicate_tool",
        "tool_output_echo",
        "tool_failure_stop",
        "tool_budget_exhausted",
        "plan_exhausted",
        "read_only_no_edits",
        "phase_cycle_cap",
    }
)


@dataclass
class EvalTask:
    """One golden task: seed a workspace, ask for a change, verify objectively."""

    id: str
    title: str
    description: str
    acceptance_criteria: List[str] = field(default_factory=list)
    seed_files: Dict[str, str] = field(default_factory=dict)
    verify_command: str = ""
    expect_changed_files: List[str] = field(default_factory=list)
    # Files the agent must not touch (the verify test itself), so a task cannot be
    # "passed" by rewriting its own assertions.
    protected_files: List[str] = field(default_factory=list)
    start_lane: str = "In Progress"
    max_steps: int = DEFAULT_MAX_STEPS
    brief: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvalTask":
        missing = [k for k in ("id", "title") if not str(data.get(k) or "").strip()]
        if missing:
            raise ValueError(f"Eval task missing required field(s): {', '.join(missing)}")
        return cls(
            id=str(data["id"]).strip(),
            title=str(data["title"]).strip(),
            description=str(data.get("description") or "").strip(),
            acceptance_criteria=[str(x) for x in (data.get("acceptanceCriteria") or [])],
            seed_files={str(k): str(v) for k, v in (data.get("seedFiles") or {}).items()},
            verify_command=str(data.get("verifyCommand") or "").strip(),
            expect_changed_files=[str(x) for x in (data.get("expectChangedFiles") or [])],
            protected_files=[str(x) for x in (data.get("protectedFiles") or [])],
            start_lane=str(data.get("startLane") or "In Progress"),
            max_steps=int(data.get("maxSteps") or DEFAULT_MAX_STEPS),
            brief=str(data.get("brief") or "").strip(),
        )


@dataclass
class StepEconomics:
    """Per-step numbers pulled from the diagnostics JSON the pipeline already writes."""

    exit_reason: str = ""
    ok: bool = False
    duration_ms: int = 0
    llm_iterations_used: int = 0
    llm_iterations_max: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    ollama_calls: int = 0
    ollama_ms_total: int = 0
    eval_tokens: int = 0
    prompt_tokens: int = 0
    tokens_reported: bool = False
    tool_recovery_events: int = 0
    plan_rejections: int = 0
    text_rejections: int = 0

    @property
    def tokens_per_sec(self) -> Optional[float]:
        """Generation throughput. None when the server did not report token counts."""
        if not self.tokens_reported or self.ollama_ms_total <= 0 or self.eval_tokens <= 0:
            return None
        return round(self.eval_tokens / (self.ollama_ms_total / 1000.0), 2)


@dataclass
class EvalResult:
    task_id: str
    title: str
    passed: bool
    verify_passed: bool
    files_changed_ok: bool
    tampered: bool
    reached_done: bool
    steps_run: int
    wall_clock_sec: float
    changed_files: List[str] = field(default_factory=list)
    verify_output: str = ""
    failure_reason: str = ""
    steps: List[StepEconomics] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["steps"] = [
            {**asdict(s), "tokensPerSec": s.tokens_per_sec} for s in self.steps
        ]
        return data


def load_tasks(tasks_dir: Optional[Path] = None, only: Optional[Sequence[str]] = None) -> List[EvalTask]:
    """Load golden task definitions from a directory of JSON files."""
    directory = Path(tasks_dir) if tasks_dir else DEFAULT_TASKS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"Eval tasks directory not found: {directory}")
    wanted = {str(x) for x in only} if only else None
    tasks: List[EvalTask] = []
    for path in sorted(directory.glob("*.json")):
        with open(path, "r", encoding="utf-8") as handle:
            task = EvalTask.from_dict(json.load(handle))
        if wanted and task.id not in wanted:
            continue
        tasks.append(task)
    if wanted:
        found = {t.id for t in tasks}
        for missing in sorted(wanted - found):
            raise ValueError(f"Unknown eval task id: {missing}")
    return tasks


def seed_workspace(task: EvalTask, root: Path) -> Dict[str, float]:
    """Write the task's seed files and return {relpath: mtime} for change detection."""
    stamps: Dict[str, float] = {}
    for rel, content in task.seed_files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        stamps[rel] = target.stat().st_mtime
    return stamps


def detect_changed_files(root: Path, before: Dict[str, float]) -> List[str]:
    """Relative paths that were created or modified since seeding."""
    changed: List[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        prior = before.get(rel)
        if prior is None or path.stat().st_mtime > prior:
            changed.append(rel)
    return changed


def run_verify(task: EvalTask, root: Path, timeout_sec: int = DEFAULT_VERIFY_TIMEOUT_SEC) -> tuple[bool, str]:
    """Run the task's verify command inside the workspace. No command means no gate."""
    if not task.verify_command:
        return True, "(no verify command)"
    try:
        proc = subprocess.run(
            task.verify_command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, f"verify timed out after {timeout_sec}s"
    except OSError as exc:
        return False, f"verify could not run: {exc}"
    output = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    return proc.returncode == 0, output


def _read_diagnostics_since(project_id: str, since_ts: float) -> List[Dict[str, Any]]:
    """Diagnostics JSON written during this eval run, oldest first."""
    try:
        folder = diagnostics_dir(project_id)
    except Exception:
        return []
    payloads: List[tuple[float, Dict[str, Any]]] = []
    for path in folder.glob("step-*.json"):
        try:
            mtime = path.stat().st_mtime
            if mtime < since_ts:
                continue
            payloads.append((mtime, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    payloads.sort(key=lambda item: item[0])
    return [p for _, p in payloads]


def economics_from_diagnostics(payload: Dict[str, Any]) -> StepEconomics:
    """Project one diagnostics JSON payload onto the metrics the eval cares about."""
    iterations = payload.get("llmIterations") or {}
    events = payload.get("events") or []
    recovery = sum(
        1
        for e in events
        if isinstance(e, dict) and e.get("kind") == "tool_calls_recovered_from_content"
    )
    return StepEconomics(
        exit_reason=str(payload.get("exitReason") or ""),
        ok=bool(payload.get("ok")),
        duration_ms=int(payload.get("durationMs") or 0),
        llm_iterations_used=int(iterations.get("used") or 0),
        llm_iterations_max=int(iterations.get("max") or 0),
        tool_calls=len(payload.get("toolsLog") or []),
        tool_failures=int(payload.get("toolFailures") or 0),
        ollama_calls=int(payload.get("ollamaCallCount") or 0),
        ollama_ms_total=int(payload.get("ollamaMsTotal") or 0),
        eval_tokens=int(payload.get("evalTokensTotal") or 0),
        prompt_tokens=int(payload.get("promptTokensTotal") or 0),
        tokens_reported=bool(payload.get("tokensReported")),
        tool_recovery_events=recovery,
        plan_rejections=int(payload.get("planRejections") or 0),
        text_rejections=int(payload.get("textRejections") or 0),
    )


def _build_card(task: EvalTask) -> Dict[str, Any]:
    from backend.agents.task_context import init_new_task

    card: Dict[str, Any] = {
        "id": f"EVAL-{task.id}",
        "title": task.title,
        "description": task.description,
        "acceptanceCriteria": list(task.acceptance_criteria),
        "status": task.start_lane,
        "assignee": "Developer",
        "createdBy": "po",
    }
    init_new_task(card)
    return card


def _default_step_runner(brief: str) -> None:
    from backend.services.sprint_service import run_sprint_step

    run_sprint_step(brief, str(getattr(state, "OLLAMA_URL", "") or ""))


def run_eval_task(
    task: EvalTask,
    *,
    step_runner: Optional[Callable[[str], None]] = None,
    workspace_root: Optional[Path] = None,
    verify_timeout_sec: int = DEFAULT_VERIFY_TIMEOUT_SEC,
) -> EvalResult:
    """Run one golden task end to end against a disposable workspace."""
    runner = step_runner or _default_step_runner
    started = time.time()
    owns_workspace = workspace_root is None
    root = Path(workspace_root) if workspace_root else Path(tempfile.mkdtemp(prefix=f"eval-{task.id}-"))
    root.mkdir(parents=True, exist_ok=True)

    prior_workspace = state.WORKSPACE_DIR
    prior_board = state.SHARED_BOARD
    prior_cancel = state.SPRINT_CANCEL
    prior_active_task = state.ACTIVE_SPRINT_TASK_ID
    prior_active_agent = state.ACTIVE_SPRINT_AGENT

    steps: List[StepEconomics] = []
    steps_run = 0
    reached_done = False
    failure_reason = ""

    try:
        before = seed_workspace(task, root)
        state.WORKSPACE_DIR = str(root)
        state.SPRINT_CANCEL = False
        state.ACTIVE_SPRINT_TASK_ID = None
        state.ACTIVE_SPRINT_AGENT = None

        card = _build_card(task)
        state.SHARED_BOARD = {
            "Features": [],
            "Backlog": [],
            "Blocked": [],
            "In Progress": [],
            "Needs PO": [],
            "Needs User": [],
            "Code Review": [],
            "QA": [],
            "Done": [],
        }
        state.SHARED_BOARD.setdefault(task.start_lane, []).append(card)

        brief = task.brief or task.description or task.title
        for _ in range(max(1, task.max_steps)):
            if state.SPRINT_CANCEL:
                failure_reason = "sprint cancelled"
                break
            steps_run += 1
            try:
                runner(brief)
            except Exception as exc:  # a crashing step is a failed task, not a failed run
                failure_reason = f"step raised {type(exc).__name__}: {exc}"
                break
            done_ids = {t.get("id") for t in state.SHARED_BOARD.get("Done", [])}
            if card["id"] in done_ids:
                reached_done = True
                break
            blocked = state.SHARED_BOARD.get("Needs User", []) + state.SHARED_BOARD.get("Needs PO", [])
            if any(t.get("id") == card["id"] for t in blocked):
                failure_reason = failure_reason or "card escalated to Needs User/Needs PO"
                break

        changed = detect_changed_files(root, before)
        tampered_files = sorted(set(task.protected_files) & set(changed))
        tampered = bool(tampered_files)
        verify_passed, verify_output = run_verify(task, root, timeout_sec=verify_timeout_sec)
        expected = set(task.expect_changed_files)
        files_changed_ok = expected.issubset(set(changed)) if expected else bool(changed)

        steps = [economics_from_diagnostics(p) for p in _read_diagnostics_since(state.CURRENT_PROJECT_ID, started)]

        passed = verify_passed and files_changed_ok and not tampered
        if not passed and not failure_reason:
            if tampered:
                failure_reason = f"modified protected file(s): {', '.join(tampered_files)}"
            elif not files_changed_ok:
                missing = sorted(expected - set(changed))
                failure_reason = f"expected file(s) unchanged: {', '.join(missing) or 'none changed'}"
            else:
                failure_reason = "verify command failed"

        return EvalResult(
            task_id=task.id,
            title=task.title,
            passed=passed,
            verify_passed=verify_passed,
            files_changed_ok=files_changed_ok,
            tampered=tampered,
            reached_done=reached_done,
            steps_run=steps_run,
            wall_clock_sec=round(time.time() - started, 1),
            changed_files=changed,
            verify_output=verify_output,
            failure_reason=failure_reason,
            steps=steps,
        )
    finally:
        state.WORKSPACE_DIR = prior_workspace
        state.SHARED_BOARD = prior_board
        state.SPRINT_CANCEL = prior_cancel
        state.ACTIVE_SPRINT_TASK_ID = prior_active_task
        state.ACTIVE_SPRINT_AGENT = prior_active_agent
        if owns_workspace:
            shutil.rmtree(root, ignore_errors=True)


def summarize(results: Sequence[EvalResult]) -> Dict[str, Any]:
    """Aggregate a suite run into the handful of numbers worth tracking over time."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    all_steps = [s for r in results for s in r.steps]
    throughputs = [s.tokens_per_sec for s in all_steps if s.tokens_per_sec is not None]
    budget_exhausted = sum(1 for s in all_steps if s.exit_reason in BUDGET_EXHAUSTED_EXITS)
    recovery_steps = sum(1 for s in all_steps if s.tool_recovery_events > 0)

    exit_reasons: Dict[str, int] = {}
    for step in all_steps:
        key = step.exit_reason or "(unknown)"
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    return {
        "tasks": total,
        "passed": passed,
        "failed": total - passed,
        "passRate": round(passed / total, 3) if total else 0.0,
        "tamperedTasks": sum(1 for r in results if r.tampered),
        "totalSteps": len(all_steps),
        "budgetExhaustedSteps": budget_exhausted,
        "budgetExhaustedRate": round(budget_exhausted / len(all_steps), 3) if all_steps else 0.0,
        # High recovery rate means the model is not emitting native tool calls reliably.
        "toolRecoverySteps": recovery_steps,
        "toolRecoveryRate": round(recovery_steps / len(all_steps), 3) if all_steps else 0.0,
        "avgTokensPerSec": round(sum(throughputs) / len(throughputs), 2) if throughputs else None,
        "totalWallClockSec": round(sum(r.wall_clock_sec for r in results), 1),
        "exitReasons": dict(sorted(exit_reasons.items(), key=lambda kv: -kv[1])),
    }


def format_report(results: Sequence[EvalResult], summary: Dict[str, Any]) -> str:
    """Human-readable markdown summary for the terminal / a run log."""
    lines: List[str] = []
    lines.append("# Eval run")
    lines.append("")
    lines.append(
        f"**{summary['passed']}/{summary['tasks']} passed** "
        f"({summary['passRate'] * 100:.0f}%) in {summary['totalWallClockSec']}s"
    )
    lines.append("")
    tps = summary["avgTokensPerSec"]
    lines.append(f"- Avg generation: {tps if tps is not None else 'not reported'} tok/s")
    lines.append(
        f"- Steps hitting a budget wall: {summary['budgetExhaustedSteps']}/{summary['totalSteps']} "
        f"({summary['budgetExhaustedRate'] * 100:.0f}%)"
    )
    lines.append(
        f"- Steps needing tool-call recovery: {summary['toolRecoverySteps']}/{summary['totalSteps']} "
        f"({summary['toolRecoveryRate'] * 100:.0f}%)"
    )
    lines.append("")
    lines.append("| Task | Result | Steps | Sec | Notes |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in results:
        mark = "pass" if r.passed else "FAIL"
        note = r.failure_reason or (r.steps[-1].exit_reason if r.steps else "")
        lines.append(f"| {r.task_id} | {mark} | {r.steps_run} | {r.wall_clock_sec} | {note} |")
    if summary["exitReasons"]:
        lines.append("")
        lines.append("Exit reasons: " + ", ".join(f"{k} x{v}" for k, v in summary["exitReasons"].items()))
    return "\n".join(lines)


def run_suite(
    tasks: Sequence[EvalTask],
    *,
    step_runner: Optional[Callable[[str], None]] = None,
    verify_timeout_sec: int = DEFAULT_VERIFY_TIMEOUT_SEC,
    on_result: Optional[Callable[[EvalResult], None]] = None,
) -> tuple[List[EvalResult], Dict[str, Any]]:
    results: List[EvalResult] = []
    for task in tasks:
        result = run_eval_task(
            task,
            step_runner=step_runner,
            verify_timeout_sec=verify_timeout_sec,
        )
        results.append(result)
        if on_result:
            on_result(result)
    return results, summarize(results)


def write_run_artifacts(
    results: Sequence[EvalResult],
    summary: Dict[str, Any],
    out_dir: Path,
    *,
    label: str = "",
) -> Path:
    """Persist the raw JSON + markdown report so runs can be diffed over time."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = f"{stamp}{'-' + label if label else ''}"
    payload = {
        "label": label,
        "timestamp": stamp,
        "summary": summary,
        "results": [r.to_dict() for r in results],
    }
    json_path = out_dir / f"eval-{slug}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / f"eval-{slug}.md").write_text(format_report(results, summary), encoding="utf-8")
    return json_path
