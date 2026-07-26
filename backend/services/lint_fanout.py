"""Fan leftover lint/analyze findings into related Backlog cards (hybrid budget)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from backend import state
from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
_OPEN_LANES_SKIP = frozenset({"Done"})


def _severity_rank(item: Dict[str, Any]) -> int:
    return _SEVERITY_RANK.get(str(item.get("severity") or "info").lower(), 9)


def sort_diagnostics(diagnostics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [d for d in diagnostics if isinstance(d, dict)],
        key=lambda d: (
            _severity_rank(d),
            str(d.get("file") or ""),
            int(d.get("line") or 0),
            str(d.get("message") or ""),
        ),
    )


def budget_diagnostics(
    diagnostics: List[Dict[str, Any]],
    *,
    max_keep: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split into in-card keep list and remainder (severity-sorted)."""
    ws = get_workflow_settings()
    keep_n = max(0, int(max_keep if max_keep is not None else ws.get("maxInCardLintFixes", 5)))
    ordered = sort_diagnostics(diagnostics)
    return ordered[:keep_n], ordered[keep_n:]


def format_budgeted_problems(diagnostics: List[Dict[str, Any]], *, max_keep: Optional[int] = None) -> str:
    kept, _rest = budget_diagnostics(diagnostics, max_keep=max_keep)
    if not kept:
        return ""
    lines = ["## Problems (fix these first — in-card budget)", ""]
    for item in kept:
        severity = item.get("severity", "info")
        file_path = item.get("file", "?")
        line = item.get("line", 0)
        column = item.get("column", 0)
        message = item.get("message", "")
        lines.append(f"- {file_path}:{line}:{column}  {severity}  {message}")
    return "\n".join(lines)


def _existing_open_lint_files(parent_id: str) -> Set[str]:
    """Paths already covered by open related lint cards (or any open lintSourceFile)."""
    found: Set[str] = set()
    parent = find_task_by_id(parent_id)
    related = set(str(r) for r in (parent.get("relatedTaskIds") or []) if r) if parent else set()
    for lane, tasks in (state.SHARED_BOARD or {}).items():
        if lane in _OPEN_LANES_SKIP:
            continue
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            if not isinstance(t, dict):
                continue
            src = str(t.get("lintSourceFile") or "").strip().replace("\\", "/")
            if not src:
                # Also match title convention Lint: path
                title = str(t.get("title") or "")
                if title.startswith("Lint: "):
                    src = title[6:].strip().replace("\\", "/")
            if not src:
                continue
            tid = str(t.get("id") or "")
            if tid == parent_id:
                continue
            # Prefer related cards; also treat any open Lint: card for same file as dedupe
            if tid in related or t.get("lintSourceFile") or str(t.get("title") or "").startswith("Lint: "):
                found.add(src)
    return found


def _group_by_file(diagnostics: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for d in sort_diagnostics(diagnostics):
        path = str(d.get("file") or "").strip().replace("\\", "/") or "(unknown)"
        groups[path].append(d)
    # Sort groups by worst severity then path
    def group_key(item: Tuple[str, List[Dict[str, Any]]]) -> Tuple[int, str]:
        path, items = item
        best = min((_severity_rank(i) for i in items), default=9)
        return best, path

    return sorted(groups.items(), key=group_key)


def _finding_bullet(d: Dict[str, Any]) -> str:
    sev = d.get("severity", "info")
    line = d.get("line", 0)
    col = d.get("column", 0)
    msg = d.get("message", "")
    return f"- {line}:{col} {sev}: {msg}"


def _clip_desc(desc: str, limit: int = 780) -> str:
    """Stay under board oversize description limit (800)."""
    if len(desc) <= limit:
        return desc
    return desc[: limit - 1] + "…"


def _build_file_card(parent_id: str, path: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    bullets: List[str] = []
    for d in findings:
        bullets.append(_finding_bullet(d))
        draft = (
            f"Lint follow-up from {parent_id}. File: {path}\n\n"
            f"Findings:\n" + "\n".join(bullets) + "\n\n"
            "Fix diagnostics for this file only — not project-wide cleanup."
        )
        if len(draft) > 780:
            bullets.pop()
            break
    omitted = len(findings) - len(bullets)
    extra = f"\n(+{omitted} more omitted)" if omitted > 0 else ""
    desc = _clip_desc(
        f"Lint follow-up from {parent_id}. File: {path}\n\n"
        f"Findings:\n" + "\n".join(bullets) + extra + "\n\n"
        "Fix diagnostics for this file only — not project-wide cleanup."
    )
    return {
        "title": f"Lint: {path}",
        "description": desc,
        "acceptanceCriteria": [
            f"Diagnostics for {path} are resolved",
            "Lint/analyze clean for this file",
        ],
        "workType": "implementation",
        "requiresDev": True,
        "requiresQa": False,
        "lintSourceFile": path,
        "relatedTaskIds": [parent_id],
        "priority": 2,
    }


def _build_overflow_card(
    parent_id: str,
    leftover_groups: List[Tuple[str, List[Dict[str, Any]]]],
) -> Dict[str, Any]:
    lines: List[str] = []
    total = 0
    for path, findings in leftover_groups:
        total += len(findings)
        lines.append(f"- {path}: {len(findings)}")
        draft = (
            f"Lint overflow from {parent_id}. "
            f"{len(leftover_groups)} file(s) / ~{total} finding(s).\n\n"
            + "\n".join(lines)
            + "\n\nSplit or fix remaining lint for these files."
        )
        if len(draft) > 780:
            lines.pop()
            break
    desc = _clip_desc(
        f"Lint overflow from {parent_id}. "
        f"{len(leftover_groups)} file(s) beyond fan-out cap.\n\n"
        + "\n".join(lines)
        + "\n\nSplit or fix remaining lint for these files."
    )
    return {
        "title": "Lint: overflow (remaining files)",
        "description": desc,
        "acceptanceCriteria": [
            "Overflow lint findings resolved or split into focused cards",
        ],
        "workType": "implementation",
        "requiresDev": True,
        "requiresQa": False,
        "lintSourceFile": "__overflow__",
        "relatedTaskIds": [parent_id],
        "priority": 2,
    }


def maybe_fanout_lint_diagnostics(
    task: Dict[str, Any],
    diagnostics: Optional[List[Dict[str, Any]]] = None,
    *,
    step_marker: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If findings exceed threshold, keep an in-card budget on the parent and spawn
    related Backlog cards for the remainder (grouped by file).

    Trims parent lastCommandDiagnostics to the kept subset so requireCleanLint
    does not block the feature on project-wide leftovers after fan-out.
    """
    parent_id = str(task.get("id") or "")
    if not parent_id:
        return {"kept": [], "spawned": [], "skipped": "no_task_id", "remainder": []}

    ws = get_workflow_settings()
    threshold = max(1, int(ws.get("lintFanoutThreshold", 6)))
    max_keep = max(0, int(ws.get("maxInCardLintFixes", 5)))
    max_cards = max(1, int(ws.get("maxLintFanoutCards", 8)))

    diags = diagnostics if diagnostics is not None else list(task.get("lastCommandDiagnostics") or [])
    if not isinstance(diags, list):
        diags = []
    diags = [d for d in diags if isinstance(d, dict)]

    marker = step_marker if step_marker is not None else (state.SPRINT_STEP_STARTED_AT or "")
    if marker and task.get("lintFanoutStepMarker") == marker:
        kept, remainder = budget_diagnostics(diags, max_keep=max_keep)
        return {
            "kept": kept,
            "spawned": [],
            "skipped": "already_this_step",
            "remainder": remainder,
        }

    kept, remainder = budget_diagnostics(diags, max_keep=max_keep)

    if len(diags) < threshold:
        # Below threshold: leave diagnostics as-is; no spawn.
        return {
            "kept": sort_diagnostics(diags),
            "spawned": [],
            "skipped": "below_threshold",
            "remainder": [],
        }

    # Mark step so fix-verify + end-of-step hooks do not double-fanout.
    task["lintFanoutStepMarker"] = marker or "once"

    # Trim parent diagnostics to in-card budget (project leftovers become follow-up cards).
    board_parent = find_task_by_id(parent_id) or task
    board_parent["lastCommandDiagnostics"] = list(kept)
    task["lastCommandDiagnostics"] = list(kept)

    if not remainder:
        return {"kept": kept, "spawned": [], "skipped": "nothing_to_spawn", "remainder": []}

    existing = _existing_open_lint_files(parent_id)
    groups = _group_by_file(remainder)
    to_spawn: List[Dict[str, Any]] = []
    skipped_dup = 0
    overflow_groups: List[Tuple[str, List[Dict[str, Any]]]] = []

    file_card_budget = max_cards
    # Reserve one slot for overflow if we will exceed
    file_paths_needed = [g for g in groups if g[0] not in existing]
    skipped_dup = sum(1 for g in groups if g[0] in existing)

    if len(file_paths_needed) > file_card_budget:
        # Use last slot for overflow card
        file_slots = max(0, file_card_budget - 1)
        for path, findings in file_paths_needed[:file_slots]:
            to_spawn.append(_build_file_card(parent_id, path, findings))
        overflow_groups = file_paths_needed[file_slots:]
        if overflow_groups:
            # Dedupe overflow card
            if "__overflow__" not in existing:
                to_spawn.append(_build_overflow_card(parent_id, overflow_groups))
    else:
        for path, findings in file_paths_needed:
            to_spawn.append(_build_file_card(parent_id, path, findings))

    spawned_ids: List[str] = []
    if to_spawn:
        from backend.services.board_service import append_backlog_tasks

        # Do NOT pass split_from_task_id — parent must stay on the board.
        msg = append_backlog_tasks(to_spawn)
        # Collect spawned ids by lintSourceFile
        wanted = {str(c.get("lintSourceFile") or "") for c in to_spawn}
        for lane, tasks in (state.SHARED_BOARD or {}).items():
            if lane in _OPEN_LANES_SKIP:
                continue
            if not isinstance(tasks, list):
                continue
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                src = str(t.get("lintSourceFile") or "")
                if src in wanted and str(t.get("id")) != parent_id:
                    spawned_ids.append(str(t["id"]))
                    # Ensure bidirectional related links
                    rel = list(t.get("relatedTaskIds") or [])
                    if parent_id not in rel:
                        rel.append(parent_id)
                        t["relatedTaskIds"] = rel

        parent = find_task_by_id(parent_id) or board_parent
        related = list(parent.get("relatedTaskIds") or [])
        for sid in spawned_ids:
            if sid not in related:
                related.append(sid)
        parent["relatedTaskIds"] = related

        record_task_decision(
            parent_id,
            "Developer",
            "lint_fanout",
            f"Kept {len(kept)} finding(s) on card; spawned {len(spawned_ids)} lint follow-up(s)",
            detail=msg if isinstance(msg, str) else "",
        )
        add_system_log(
            "Developer",
            "info",
            f"{parent_id}: lint fan-out — kept {len(kept)}, spawned {len(spawned_ids)} "
            f"(skipped {skipped_dup} duplicate file card(s))",
        )
    else:
        record_task_decision(
            parent_id,
            "Developer",
            "lint_fanout",
            f"Kept {len(kept)} finding(s); no new cards (deduped or empty)",
            detail=f"skipped_dup={skipped_dup}",
        )

    return {
        "kept": kept,
        "spawned": spawned_ids,
        "skipped": "" if spawned_ids else ("deduped" if skipped_dup else "nothing_to_spawn"),
        "remainder": remainder,
        "skippedDuplicates": skipped_dup,
    }
