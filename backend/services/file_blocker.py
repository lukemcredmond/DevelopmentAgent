"""Shared-file blocker: one fix card per broken file, dependents in Blocked."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import (
    find_task_by_id,
    get_task_lane,
    normalize_task,
    record_task_decision,
)
from backend.services.lint_fanout import _build_file_card, is_junk_lint_path
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

BLOCKED_BY_KIND_FILE_FIX = "file_fix"
_OPEN_LANES_SKIP = frozenset({"Done"})
_DEPENDENT_LANES = (
    "In Progress",
    "Needs User",
    "Backlog",
    "Refinement",
    "Pending Approval",
    "QA",
    "Code Review",
    "Blocked",
    "Needs PO",
)


def normalize_file_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


_VALID_DEV_EDIT_PATH_RE = re.compile(r"^[\w./-]+$")
_MAX_DEV_EDIT_PATH_LEN = 260


def validate_dev_edit_target_path(path: str) -> bool:
    """True when path looks like a workspace-relative file, not a lint diagnostic line."""
    raw = normalize_file_path(path)
    if not raw or len(raw) > _MAX_DEV_EDIT_PATH_LEN:
        return False
    lower = raw.lower()
    if "•" in raw or "warning" in lower or raw.startswith("Error:"):
        return False
    if not _VALID_DEV_EDIT_PATH_RE.match(raw):
        return False
    return not is_junk_lint_path(raw)


def _extract_path_from_diagnostic_line(line: str) -> str:
    """Extract trailing filename from formatted lint lines."""
    raw = str(line or "").strip()
    if "•" in raw:
        tail = normalize_file_path(raw.rsplit("•", 1)[-1])
        if validate_dev_edit_target_path(tail):
            return tail
    if validate_dev_edit_target_path(raw):
        return raw
    return ""


def _title_derived_edit_path(title: str) -> str:
    title = str(title or "")
    lower = title.lower()
    for hint in ("pubspec.yaml", "analysis_options.yaml"):
        if hint in lower:
            return hint
    if "main.dart" in lower or "main app" in lower:
        return "lib/main.dart"
    match = re.search(r"(lib/[\w./-]+\.(?:dart|yaml))", title, re.I)
    if match:
        candidate = normalize_file_path(match.group(1))
        if validate_dev_edit_target_path(candidate):
            return candidate
    return ""


def infer_lint_source_file(task: Dict[str, Any]) -> str:
    """Set lintSourceFile from diagnostics when missing (e.g. flutter_lints URI errors)."""
    if not isinstance(task, dict):
        return ""
    existing = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if existing and existing not in ("(unknown)", "__overflow__"):
        return existing
    blob = " ".join(
        str(d.get(field) or "")
        for d in (task.get("lastCommandDiagnostics") or [])
        if isinstance(d, dict)
        for field in ("file", "path", "message")
    )
    lower = blob.lower()
    if "package:flutter_lints/flutter.yaml" in lower or "analysis_options.yaml" in lower:
        return "analysis_options.yaml"
    if "pubspec.yaml" in lower:
        return "pubspec.yaml"
    title_lower = str(task.get("title") or "").lower()
    if "undefined class" in lower and "widget" in lower:
        if "main.dart" in lower or "main.dart" in title_lower:
            return "lib/main.dart"
    return ""


def resolve_dev_edit_target_path(task: Dict[str, Any]) -> str:
    """Resolve a safe workspace file for synthetic read / text-loop hints."""
    if not isinstance(task, dict):
        return ""
    src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if src and src not in ("(unknown)", "__overflow__") and validate_dev_edit_target_path(src):
        return src
    title = str(task.get("title") or "")
    if title.startswith("Lint: "):
        path = normalize_file_path(title[6:])
        if validate_dev_edit_target_path(path):
            return path
    for key in ("writePaths", "scaffoldedFiles"):
        for raw in task.get(key) or []:
            path = normalize_file_path(str(raw or ""))
            if validate_dev_edit_target_path(path):
                return path
    inferred = infer_lint_source_file(task)
    if inferred and validate_dev_edit_target_path(inferred):
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = inferred
        return inferred
    for diagnostic in reversed(task.get("lastCommandDiagnostics") or []):
        if not isinstance(diagnostic, dict):
            continue
        for field in ("file", "path"):
            raw = str(diagnostic.get(field) or "")
            if validate_dev_edit_target_path(raw):
                return normalize_file_path(raw)
            extracted = _extract_path_from_diagnostic_line(raw)
            if extracted:
                return extracted
    derived = _title_derived_edit_path(title)
    if derived:
        return derived
    return ""


def dev_edit_path_explicitly_allowed(task: Dict[str, Any], path: str) -> bool:
    """True when path is in scope even if it differs from resolve_dev_edit_target_path."""
    norm = normalize_file_path(path)
    if not norm:
        return False
    fix_for = normalize_file_path(str(task.get("fileFixFor") or ""))
    if fix_for and fix_for == norm:
        return True
    title = str(task.get("title") or "").lower()
    if norm == "pubspec.yaml" and ("pubspec" in title or "sdk constraint" in title):
        return True
    if norm == "analysis_options.yaml" and "analysis_options" in title:
        return True
    scoped_src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if scoped_src and scoped_src == norm:
        return True
    return False


def dev_edit_path_allowed_for_tool(task: Dict[str, Any], path: str) -> tuple[bool, str]:
    """Gate Developer apply_patch/write_file to the card's resolved edit target."""
    norm = normalize_file_path(path)
    if not norm or not validate_dev_edit_target_path(norm):
        return False, f"Invalid or missing edit path: {path or '(empty)'}"
    target = resolve_dev_edit_target_path(task)
    if not target:
        return True, ""
    if normalize_file_path(target) == norm:
        return True, ""
    if dev_edit_path_explicitly_allowed(task, norm):
        return True, ""
    return False, f"This card is scoped to `{target}`; do not edit `{norm}`."


def is_file_blocker_wait(task: Dict[str, Any]) -> bool:
    return str(task.get("blockedByKind") or "") == BLOCKED_BY_KIND_FILE_FIX


def _task_file_path(task: Dict[str, Any]) -> str:
    src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if src and src not in ("(unknown)", "__overflow__"):
        return src
    title = str(task.get("title") or "")
    if title.startswith("Lint: "):
        path = normalize_file_path(title[6:])
        if path and not path.startswith("overflow"):
            return path
    for d in task.get("lastCommandDiagnostics") or []:
        if isinstance(d, dict):
            f = normalize_file_path(str(d.get("file") or ""))
            if f and not is_junk_lint_path(f):
                return f
    for p in task.get("writePaths") or []:
        f = normalize_file_path(str(p or ""))
        if f:
            return f
    return ""


def _task_touches_file(task: Dict[str, Any], path: str) -> bool:
    norm = normalize_file_path(path)
    if not norm:
        return False
    if normalize_file_path(_task_file_path(task)) == norm:
        return True
    title = str(task.get("title") or "")
    if title == f"Lint: {norm}":
        return True
    for d in task.get("lastCommandDiagnostics") or []:
        if isinstance(d, dict) and normalize_file_path(str(d.get("file") or "")) == norm:
            return True
    for p in task.get("writePaths") or []:
        if normalize_file_path(str(p or "")) == norm:
            return True
    return False


def _iter_open_tasks() -> List[Tuple[str, Dict[str, Any]]]:
    found: List[Tuple[str, Dict[str, Any]]] = []
    for lane, tasks in (state.SHARED_BOARD or {}).items():
        if lane in _OPEN_LANES_SKIP:
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, dict):
                found.append((lane, task))
    return found


def _find_fix_card(path: str) -> Optional[Dict[str, Any]]:
    norm = normalize_file_path(path)
    explicit: Optional[Dict[str, Any]] = None
    lint_title: Optional[Dict[str, Any]] = None
    fallback: Optional[Dict[str, Any]] = None
    for _lane, task in _iter_open_tasks():
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if normalize_file_path(str(task.get("fileFixFor") or "")) == norm:
            explicit = task
            break
        title = str(task.get("title") or "")
        if title == f"Lint: {norm}":
            if lint_title is None:
                lint_title = task
        elif normalize_file_path(_task_file_path(task)) == norm:
            if fallback is None:
                fallback = task
    return explicit or lint_title or fallback


def _create_fix_card_payload(
    path: str,
    diagnostics: Optional[List[Dict[str, Any]]],
    source_task_id: str,
) -> Dict[str, Any]:
    norm = normalize_file_path(path)
    diags: List[Dict[str, Any]] = []
    if diagnostics:
        diags = [
            d
            for d in diagnostics
            if isinstance(d, dict)
            and normalize_file_path(str(d.get("file") or "")) == norm
        ]
    if not diags:
        diags = [
            {
                "file": norm,
                "line": 0,
                "column": 0,
                "severity": "error",
                "message": "Fix diagnostics for this file",
            }
        ]
    card = _build_file_card(source_task_id or "file-blocker", norm, diags)
    card["priority"] = 1
    card["fileFixFor"] = norm
    card["lintSourceFile"] = norm
    return card


def _clear_needs_user_fields(task: Dict[str, Any]) -> None:
    task["userQuestion"] = None
    task["needsUserReason"] = None
    task["needsUserAction"] = None
    task["needsUserKind"] = None
    task["needsUserSuggestedTarget"] = None
    task["needsUserOptions"] = None
    task["needsUserDuplicate"] = False


def _wire_dependent(task: Dict[str, Any], fix_id: str) -> bool:
    tid = str(task.get("id") or "")
    if not tid or tid == fix_id:
        return False
    lane = get_task_lane(tid) or str(task.get("status") or "Backlog")
    if lane == "Done":
        return False
    blocked = [str(x) for x in (task.get("blockedBy") or []) if x]
    if fix_id in blocked:
        return False
    blocked.append(fix_id)
    task["blockedBy"] = blocked
    task["blockedByKind"] = BLOCKED_BY_KIND_FILE_FIX
    if lane != "Blocked":
        task["blockedReturnLane"] = lane
    _clear_needs_user_fields(task)
    record_task_decision(
        tid,
        "System",
        "file_blocker",
        f"Blocked on fix card {fix_id} for shared file",
    )
    return True


def _link_related(source_task_id: str, fix_id: str) -> None:
    if not source_task_id or not fix_id:
        return
    parent = find_task_by_id(source_task_id)
    fix = find_task_by_id(fix_id)
    if parent:
        rel = list(parent.get("relatedTaskIds") or [])
        if fix_id not in rel:
            rel.append(fix_id)
            parent["relatedTaskIds"] = rel
    if fix:
        rel = list(fix.get("relatedTaskIds") or [])
        if source_task_id not in rel:
            rel.append(source_task_id)
            fix["relatedTaskIds"] = rel


def orchestrate_file_blocker(
    *,
    file_path: str,
    diagnostics: list[dict] | None = None,
    source_task_id: str = "",
    reason: str = "",
) -> dict:
    """Find or create one fix card and block dependents touching the same file."""
    ws = get_workflow_settings()
    if not ws.get("enableFileBlockerOrchestration", True):
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "disabled"}

    path = normalize_file_path(file_path)
    if not path or path in ("(unknown)", "__overflow__") or is_junk_lint_path(path):
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "invalid_path"}

    min_cards = max(1, int(ws.get("fileBlockerMinDependents", 2)))
    auto_create = bool(ws.get("fileBlockerAutoCreateFixCard", True))

    create_payload: Optional[Dict[str, Any]] = None
    with state.STATE_LOCK:
        touching = [(lane, t) for lane, t in _iter_open_tasks() if _task_touches_file(t, path)]
        fix = _find_fix_card(path)
        fix_id = str(fix.get("id") or "") if fix else ""
        if not fix_id and auto_create:
            create_payload = _create_fix_card_payload(path, diagnostics, source_task_id)

    created = False
    if create_payload:
        from backend.services.board_service import append_backlog_tasks

        append_backlog_tasks([create_payload])
        created = True

    blocked_ids: List[str] = []
    skipped = ""
    with state.STATE_LOCK:
        fix = _find_fix_card(path)
        if not fix and auto_create:
            skipped = "no_fix_card"
            fix_id = ""
        elif not fix:
            skipped = "no_fix_card"
            fix_id = ""
        else:
            fix_id = str(fix.get("id") or "")
            fix["fileFixFor"] = path
            fix["lintSourceFile"] = path
            normalize_task(fix)

        open_for_file: List[Dict[str, Any]] = [t for _lane, t in touching if isinstance(t, dict)]
        if fix and fix not in open_for_file:
            open_for_file.append(fix)

        if not fix_id:
            return {"fixTaskId": "", "blocked": [], "created": created, "skipped": skipped or "no_fix_card"}

        if len(open_for_file) < min_cards:
            _link_related(source_task_id, fix_id)
            return {
                "fixTaskId": fix_id,
                "blocked": [],
                "created": created,
                "skipped": "below_min_dependents",
            }

        for _lane, task in touching:
            tid = str(task.get("id") or "")
            if tid == fix_id:
                continue
            if _wire_dependent(task, fix_id):
                blocked_ids.append(tid)

        _link_related(source_task_id, fix_id)

        if blocked_ids:
            from backend.services.blocked_lane import sync_blocked_lane

            sync_blocked_lane(persist=True)
            detail = reason or f"Shared file {path}"
            add_system_log(
                "System",
                "info",
                f"File blocker {path}: fix {fix_id}, blocked {len(blocked_ids)} card(s) — {detail}",
            )
            record_task_decision(
                fix_id,
                "System",
                "file_blocker",
                f"Canonical fix card for {path}; {len(blocked_ids)} dependent(s) blocked",
                detail[:500],
            )

    return {
        "fixTaskId": fix_id,
        "blocked": blocked_ids,
        "created": created,
        "skipped": skipped,
    }


def orchestrate_from_task(task: Dict[str, Any], *, reason: str = "") -> dict:
    path = _task_file_path(task)
    if not path:
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "no_file"}
    diags = task.get("lastCommandDiagnostics")
    return orchestrate_file_blocker(
        file_path=path,
        diagnostics=diags if isinstance(diags, list) else None,
        source_task_id=str(task.get("id") or ""),
        reason=reason,
    )


def _is_lint_pile_candidate(task: Dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    path = _task_file_path(task)
    if not path or is_junk_lint_path(path):
        return False
    title = str(task.get("title") or "")
    if title.startswith("Lint: ") or task.get("lintSourceFile"):
        return True
    diags = task.get("lastCommandDiagnostics") or []
    return isinstance(diags, list) and len(diags) > 0


def reconcile_file_blockers_from_board() -> dict:
    """Group lint pile cards by file, create one fix card each, block dependents."""
    ws = get_workflow_settings()
    if not ws.get("enableFileBlockerOrchestration", True):
        return {"files": 0, "blocked": 0, "fixCards": 0, "skipped": "disabled"}

    min_cards = max(1, int(ws.get("fileBlockerMinDependents", 2)))
    groups: Dict[str, List[str]] = defaultdict(list)

    with state.STATE_LOCK:
        for lane in _DEPENDENT_LANES:
            for task in list(state.SHARED_BOARD.get(lane, [])):
                if not _is_lint_pile_candidate(task):
                    continue
                path = _task_file_path(task)
                tid = str(task.get("id") or "")
                if path and tid:
                    groups[path].append(tid)

    files_processed = 0
    total_blocked = 0
    fix_cards = 0
    for path, tids in groups.items():
        if len(tids) < min_cards:
            continue
        result = orchestrate_file_blocker(
            file_path=path,
            source_task_id=tids[0],
            reason="reconcile lint pile",
        )
        if result.get("fixTaskId"):
            fix_cards += 1
        blocked = result.get("blocked") or []
        if blocked:
            total_blocked += len(blocked)
            files_processed += 1

    if total_blocked:
        add_system_log(
            "System",
            "success",
            f"Reconciled lint pile: {files_processed} file(s), "
            f"{total_blocked} card(s) blocked, {fix_cards} fix card(s)",
        )

    return {
        "files": files_processed,
        "blocked": total_blocked,
        "fixCards": fix_cards,
        "skipped": "",
    }
