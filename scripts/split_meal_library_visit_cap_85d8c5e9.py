#!/usr/bin/env python3
"""Split visit-capped MealLibraryScreen into plan-sized child cards (meal planner / 85d8c5e9)."""
from __future__ import annotations

import re
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ID = "85d8c5e9-a905-4868-a773-29eb4bfa436d"
PARENT_TASK_ID = "TASK-DD13EC83C6254CD8A20C9B7769CA43AB"
TRIAGE_NOTE = "Split after Developer visit cap — split_meal_library_visit_cap_85d8c5e9.py"

MEAL_LIBRARY_CHILDREN = (
    (
        "Meal library — list screen shell and navigation",
        "Create MealLibraryScreen scaffold: route registration, app bar, body placeholder. "
        "Wire navigation from home/main. Match existing app theme and folder layout under lib/.",
        [
            "Meal library route opens from main navigation",
            "flutter analyze passes on new screen files",
        ],
    ),
    (
        "Meal library — Riverpod provider and meal list UI",
        "Provider that loads meals from existing Drift/repository layer. ListView (or grid) "
        "with title and key fields. Handle loading and empty states.",
        [
            "Provider exposes meal list from local DB without network",
            "Empty state shown when no meals exist",
        ],
    ),
    (
        "Meal library — random meal selection action",
        "Add user action (FAB or button) to pick a random meal from the library. "
        "Show result in snackbar or detail panel. Edge case: single meal and empty library.",
        [
            "Random pick selects uniformly from available meals",
            "Empty library shows clear message instead of crashing",
        ],
    ),
    (
        "Meal library — polish and analyzer clean",
        "Fix analyzer issues in meal library module. Consistent spacing, accessibility labels "
        "on primary actions. No new features — stabilize for QA.",
        ["flutter analyze reports no issues in meal library files"],
    ),
)

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
    "consecutiveBadExits",
    "identicalPatchFailCount",
    "lastCircuitExitReason",
    "circuitBreakerPoRetryUsed",
    "circuitBreakerParked",
    "splitAttemptedOnStuck",
)


def _title_key(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").lower()).strip()[:120]


def _new_task_id() -> str:
    return f"TASK-{uuid.uuid4().hex.upper()}"


def _strip_latch(task: dict) -> None:
    for key in _LATCH_KEYS:
        task.pop(key, None)
    task["devStepCount"] = 0
    task["devPhaseCycle"] = 0
    task["stuckLoops"] = 0


def _find_task(board: dict, task_id: str) -> tuple[str, int, dict] | None:
    for lane, tasks in board.items():
        if not isinstance(tasks, list):
            continue
        for idx, task in enumerate(tasks):
            if isinstance(task, dict) and str(task.get("id") or "") == task_id:
                return lane, idx, task
    return None


def _triage_duplicate_needs_po(board: dict) -> int:
    """Move Needs PO cards that shadow In Progress / Backlog titles to Blocked."""
    ip_keys = {
        _title_key(t.get("title"))
        for t in (board.get("In Progress") or [])
        if isinstance(t, dict) and _title_key(t.get("title"))
    }
    backlog_keys = {
        _title_key(t.get("title"))
        for t in (board.get("Backlog") or [])
        if isinstance(t, dict) and _title_key(t.get("title"))
    }
    shadow_keys = ip_keys | backlog_keys
    if not shadow_keys:
        return 0

    needs_po = board.get("Needs PO") or []
    if not isinstance(needs_po, list):
        return 0

    kept: list = []
    moved = 0
    for task in needs_po:
        if not isinstance(task, dict):
            kept.append(task)
            continue
        key = _title_key(task.get("title"))
        if key and key in shadow_keys:
            task = dict(task)
            _strip_latch(task)
            task["blockedReason"] = (
                "Duplicate PO card shadows active/backlog work — "
                "split_meal_library_visit_cap_85d8c5e9.py"
            )
            board.setdefault("Blocked", []).append(task)
            moved += 1
        else:
            kept.append(task)
    board["Needs PO"] = kept
    return moved


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from backend.config import DB_PATH
    from backend.storage.project_storage import ProjectStorage

    storage = ProjectStorage(DB_PATH)
    proj = storage.load_project(PROJECT_ID)
    if not proj:
        print(f"Project {PROJECT_ID} not found in {DB_PATH}", file=sys.stderr)
        return 1

    board = proj.get("board_state") or {}
    if not isinstance(board, dict):
        print("Invalid board_state", file=sys.stderr)
        return 1

    located = _find_task(board, PARENT_TASK_ID)
    if not located:
        print(f"Parent task {PARENT_TASK_ID} not on board — skipping parent split", file=sys.stderr)
    else:
        lane, idx, parent = located
        parent = deepcopy(parent)
        _strip_latch(parent)
        parent["splitSuperseded"] = True
        parent["blockedReason"] = TRIAGE_NOTE
        meta = dict(parent.get("meta") or {})
        meta["visitCapSplitAt"] = datetime.now(timezone.utc).isoformat()
        parent["meta"] = meta

        board[lane].pop(idx)
        board.setdefault("Done", []).append(parent)

        backlog = board.setdefault("Backlog", [])
        created: list[str] = []
        for title, description, ac in MEAL_LIBRARY_CHILDREN:
            tid = _new_task_id()
            backlog.append(
                {
                    "id": tid,
                    "title": title,
                    "description": description,
                    "acceptanceCriteria": ac,
                    "lane": "Backlog",
                    "workType": "implementation",
                    "requiresDev": True,
                    "requiresQa": True,
                    "splitFromTaskId": PARENT_TASK_ID,
                    "meta": {"visitCapSplitChild": True},
                }
            )
            created.append(tid)

        in_progress = board.get("In Progress") or []
        if isinstance(in_progress, list):
            board["In Progress"] = [
                t for t in in_progress if str(t.get("id") or "") != PARENT_TASK_ID
            ]

        print(f"Moved {PARENT_TASK_ID} from {lane} → Done (split superseded).")
        print("Created Backlog cards:")
        for tid, (title, _, _) in zip(created, MEAL_LIBRARY_CHILDREN):
            print(f"  {tid}: {title}")

    for lane in ("In Progress", "Needs PO", "Blocked", "Needs User"):
        for task in board.get(lane) or []:
            if isinstance(task, dict):
                _strip_latch(task)

    dup_moved = _triage_duplicate_needs_po(board)
    if dup_moved:
        print(f"Moved {dup_moved} duplicate Needs PO card(s) → Blocked.")

    storage.save_project(
        PROJECT_ID,
        proj["name"],
        proj["brief"],
        proj["workspace_dir"],
        board,
        proj.get("files") or {},
        proj.get("po_skills") or [],
        proj.get("dev_skills") or [],
        proj.get("cr_skills") or [],
        proj.get("qa_skills") or [],
        proj.get("po_model") or "",
        proj.get("dev_model") or "",
        proj.get("cr_model") or "",
        proj.get("qa_model") or "",
        proj.get("po_backup_model") or "",
        proj.get("dev_backup_model") or "",
        proj.get("cr_backup_model") or "",
        proj.get("qa_backup_model") or "",
        proj.get("plan_outline") or "",
        force_board=True,
        original_brief=proj.get("original_brief"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
