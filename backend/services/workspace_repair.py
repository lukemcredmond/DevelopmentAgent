"""Manual workspace repair: analyze, reopen bad Done cards, spawn lint follow-ups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Set

from backend import state
from backend.services.board_service import append_backlog_tasks, move_board_stage
from backend.services.card_ledger import write_oracle
from backend.services.command_result import run_workspace_command
from backend.services.logs import add_system_log
from backend.services.lint_fanout import (
    _build_file_card,
    filter_workspace_diagnostics,
)
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import derive_project_lint_command

_WORKSPACE_REPAIR_PARENT = "__workspace_repair__"
_MAX_FILES = 20

_LATCH_KEYS = (
    "poAutoSkip",
    "poAutoSkipReason",
    "phaseCycleCapReached",
    "phaseCycleCapReason",
    "phaseCycleCapAt",
    "phaseCycleCapTimestamp",
    "latchedRecoveryAttempted",
    "parkFailed",
    "lastParkBlockReason",
    "forcePatchNextDevStep",
    "forcePatchAttempted",
    "devDeferredUntilStep",
    "consecutiveNoWriteStall",
)


def _strip_latch(task: Dict[str, Any]) -> None:
    for key in _LATCH_KEYS:
        task.pop(key, None)


def _existing_lint_titles() -> Set[str]:
    titles: Set[str] = set()
    for lane, tasks in (state.SHARED_BOARD or {}).items():
        if lane in ("Done",):
            continue
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title") or "")
            if title.startswith("Lint: "):
                titles.add(title)
    return titles


def _task_owns_path(task: Dict[str, Any], path: str) -> bool:
    if not path:
        return False
    norm = path.replace("\\", "/")
    for f in task.get("files") or []:
        if norm in str(f).replace("\\", "/"):
            return True
    lint_src = str(task.get("lintSourceFile") or "").strip()
    if lint_src and (norm.endswith(lint_src) or lint_src in norm):
        return True
    title = str(task.get("title") or "")
    if path in title or norm.split("/")[-1] in title:
        return True
    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        blob = str(entry.get("content") or entry.get("summary") or "")
        if norm in blob.replace("\\", "/"):
            return True
    return False


def run_workspace_repair() -> Dict[str, Any]:
    """Run project analyze, reopen affected Done/QA cards, spawn lint cards."""
    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return {
            "ok": False,
            "error": "No lint command for this workspace",
            "findings": 0,
            "cardsReopened": [],
            "lintCardsCreated": [],
        }

    add_system_log("System", "info", f"Workspace repair: running {lint_cmd}")
    result = run_workspace_command(lint_cmd)
    diagnostics = filter_workspace_diagnostics(list(result.diagnostics or []))
    clean = result.outcome == "ok" or not diagnostics
    detail = f"{lint_cmd} — {len(diagnostics)} finding(s), outcome={result.outcome}"
    write_oracle(_WORKSPACE_REPAIR_PARENT, passed=clean, detail=detail[:500])
    state.WORKSPACE_ANALYZE_CLEAN = clean
    state.WORKSPACE_ANALYZE_FINDINGS = len(diagnostics)
    state.WORKSPACE_ANALYZE_DETAIL = detail[:800]

    by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        path = str(d.get("file") or "").strip()
        if path:
            by_file[path].append(d)

    failing_paths = list(by_file.keys())[:_MAX_FILES]
    lint_created: List[str] = []
    existing_titles = _existing_lint_titles()
    spawn_batch: List[Dict[str, Any]] = []
    ws = get_workflow_settings()
    for path in failing_paths:
        title = f"Lint: {path}"
        if title in existing_titles:
            continue
        card = _build_file_card(_WORKSPACE_REPAIR_PARENT, path, by_file[path])
        spawn_batch.append(card)
        existing_titles.add(title)
    if spawn_batch:
        msg = append_backlog_tasks(spawn_batch)
        if not str(msg).startswith("Error"):
            for card in spawn_batch:
                lint_created.append(str(card.get("title") or ""))

    reopened: List[str] = []
    for lane in ("Done", "QA"):
        for task in list((state.SHARED_BOARD or {}).get(lane) or []):
            if not isinstance(task, dict):
                continue
            if not any(_task_owns_path(task, p) for p in failing_paths):
                continue
            tid = str(task.get("id") or "")
            if not tid:
                continue
            _strip_latch(task)
            task["forcePatchNextDevStep"] = True
            task["lastCommandDiagnostics"] = [
                d for p in failing_paths for d in by_file.get(p, []) if _task_owns_path(task, p)
            ][: int(ws.get("maxInCardLintFixes", 5))]
            move_board_stage(tid, "In Progress")
            reopened.append(tid)
            add_system_log(
                "System",
                "warning",
                f"{tid}: workspace repair reopened from {lane} — analyze findings on owned files",
            )

    try:
        from backend.services.project_service import save_current_project_state

        save_current_project_state()
    except Exception:
        pass

    return {
        "ok": True,
        "clean": clean,
        "lintCommand": lint_cmd,
        "findings": len(diagnostics),
        "filesWithFindings": len(by_file),
        "cardsReopened": reopened,
        "lintCardsCreated": lint_created,
        "detail": detail[:500],
    }
