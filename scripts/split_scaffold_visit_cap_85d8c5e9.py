#!/usr/bin/env python3
"""Split visit-capped scaffold card into plan-sized child cards (meal planner 6 / 85d8c5e9)."""
from __future__ import annotations

import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ID = "85d8c5e9-a905-4868-a773-29eb4bfa436d"
PARENT_TASK_ID = "TASK-C2784B55D71C4A47B76E62F99711E984"
TRIAGE_NOTE = "Split after Developer visit cap — split_scaffold_visit_cap_85d8c5e9.py"

SCAFFOLD_CHILDREN = (
    (
        "Set up Drift schema and seed tables",
        "Add Drift database schema (meals, items, stores, aisles, shopping_items, settings). "
        "Seed minimal rows for dev. Target: lib/database/ or lib/data/ per existing project layout. "
        "Single deliverable: schema + generated drift files compile.",
        ["drift schema defines core tables", "flutter analyze passes on database module"],
    ),
    (
        "Build settings provider for theme and default store",
        "Riverpod (or existing state) provider for app settings: theme mode, default store id. "
        "Persist via shared_preferences or settings table. One module + widget hook for smoke test.",
        ["settings load on cold start without network", "default store id round-trips through persistence"],
    ),
    (
        "Offline-first boot — no network on launch",
        "Ensure app main() and providers do not perform network I/O on startup. "
        "Document offline guarantee in README snippet. Wire empty-state when no meal plan exists.",
        ["airplane mode: app opens to usable screen", "no HTTP/socket calls in startup path"],
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
        print(f"Parent task {PARENT_TASK_ID} not on board — nothing to split", file=sys.stderr)
        return 1

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
    for title, description, ac in SCAFFOLD_CHILDREN:
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
        board["In Progress"] = [t for t in in_progress if str(t.get("id") or "") != PARENT_TASK_ID]

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

    print(f"Moved {PARENT_TASK_ID} from {lane} → Done (split superseded).")
    print("Created Backlog cards:")
    for tid, (title, _, _) in zip(created, SCAFFOLD_CHILDREN):
        print(f"  {tid}: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
