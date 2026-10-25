#!/usr/bin/env python3
"""Park visit-capped polish card and free In Progress (meal planner / 85d8c5e9)."""
from __future__ import annotations

import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ID = "85d8c5e9-a905-4868-a773-29eb4bfa436d"
PARENT_TASK_ID = "TASK-634E9D1ECA8D477095B876C95F49EAC0"
TRIAGE_NOTE = "Split after Developer visit cap — split_polish_visit_cap_85d8c5e9.py"

POLISH_CHILDREN = (
    (
        "Scaffold polish — analyzer clean on lib/main and app entry",
        "Fix analyzer/lint issues in lib/main.dart and app bootstrap without new features. "
        "Use apply_patch on existing files only.",
        ["flutter analyze reports no issues in lib/main.dart and related entry files"],
    ),
    (
        "Scaffold polish — offline persistence edge cases",
        "Harden offline paths: empty DB, settings defaults, no network on cold start. "
        "Touch only persistence/settings modules under lib/.",
        [
            "cold start works with no network",
            "settings and DB defaults are consistent when tables are empty",
        ],
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
    "devStepCount",
    "devPhaseCycle",
)

SPRINT_FLAG_KEYS = (
    "poAutoSkip",
    "poAutoSkipReason",
    "devDeferredUntilStep",
    "circuitBreakerPoRetryUsed",
    "consecutiveBadExits",
    "identicalPatchFailCount",
    "forcePatchNextDevStep",
    "phaseCycleCapReached",
    "latchedRecoveryAttempted",
    "parkFailed",
    "lastParkBlockReason",
    "circuitBreakerTripped",
)


def _new_task_id() -> str:
    return f"TASK-{uuid.uuid4().hex.upper()}"


def _strip_latch(task: dict) -> None:
    for key in _LATCH_KEYS:
        task.pop(key, None)
    task["devStepCount"] = 0
    task["devPhaseCycle"] = 0


def _find_task(board: dict, task_id: str) -> tuple[str, int, dict] | None:
    for lane, tasks in board.items():
        if not isinstance(tasks, list):
            continue
        for idx, task in enumerate(tasks):
            if isinstance(task, dict) and str(task.get("id") or "") == task_id:
                return lane, idx, task
    return None


def _clear_needs_po_stalls(board: dict) -> int:
    cleared = 0
    for task in board.get("Needs PO") or []:
        if not isinstance(task, dict):
            continue
        if any(task.get(k) for k in SPRINT_FLAG_KEYS if k != "phaseCycleCapReached"):
            for key in SPRINT_FLAG_KEYS:
                task.pop(key, None)
            cleared += 1
        elif task.get("poAutoSkip") or task.get("phaseCycleCapReached"):
            for key in SPRINT_FLAG_KEYS:
                task.pop(key, None)
            cleared += 1
    return cleared


def _promote_one_needs_po_to_in_progress(board: dict) -> str | None:
    ip = board.get("In Progress") or []
    if isinstance(ip, list) and ip:
        return None
    candidates = board.get("Needs PO") or []
    if not isinstance(candidates, list) or not candidates:
        return None
    preferred_titles = (
        "Set up Drift schema",
        "flutter_test",
        "Drift schema",
    )
    pick: dict | None = None
    for task in candidates:
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or "")
        if any(p in title for p in preferred_titles):
            pick = task
            break
    if pick is None:
        pick = next((t for t in candidates if isinstance(t, dict)), None)
    if not pick:
        return None
    tid = str(pick.get("id") or "")
    board["Needs PO"] = [t for t in candidates if str(t.get("id") or "") != tid]
    _strip_latch(pick)
    pick["lane"] = "In Progress"
    board.setdefault("In Progress", []).append(pick)
    return tid


def _save(storage, proj, board) -> None:
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
    if located:
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
        for title, description, ac in POLISH_CHILDREN:
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
        print(f"Moved {PARENT_TASK_ID} from {lane} → Done (split superseded).")
        for tid, (title, _, _) in zip(created, POLISH_CHILDREN):
            print(f"  Backlog: {tid}: {title}")
    else:
        print(f"Parent {PARENT_TASK_ID} not on board — skipping split.")

    cleared = _clear_needs_po_stalls(board)
    promoted = _promote_one_needs_po_to_in_progress(board)
    _save(storage, proj, board)
    print(f"Cleared sprint stall flags on {cleared} Needs PO card(s).")
    if promoted:
        print(f"Promoted {promoted} from Needs PO → In Progress.")
    else:
        print("No Needs PO promotion (In Progress already occupied or queue empty).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
