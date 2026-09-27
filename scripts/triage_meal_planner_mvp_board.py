#!/usr/bin/env python3
"""One-shot board triage for meal planner MVP last run (project c6196c51)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ID = "c6196c51-11ec-4349-93b4-2282563bdc20"
CHURN_IDS = frozenset(
    {
        "TASK-3169816AA95C469BB5A96E0859724D98",
        "TASK-7E4310632FC54552B12880878AD2DC3F",
    }
)
MVP_CARDS = (
    ("MVP-IMPORT-JSON", "MVP: Import JSON via file_picker", "In Progress"),
    ("MVP-PERSIST-DB", "MVP: Persist imported meals", "Backlog"),
    ("MVP-SHOPPING-UI", "MVP: Shopping list UI", "Backlog"),
)
FREEZE_NOTE = "Frozen for MVP last run — triage_meal_planner_mvp_board.py"

_LATCH_KEYS = (
    "poAutoSkip",
    "poAutoSkipReason",
    "phaseCycleCapReached",
    "latchedRecoveryAttempted",
    "parkFailed",
    "devDeferredUntilStep",
)


def _strip_freeze_latch_flags(task: dict) -> None:
    for key in _LATCH_KEYS:
        task.pop(key, None)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from backend.config import DB_PATH
    from backend.storage.project_storage import ProjectStorage

    storage = ProjectStorage(DB_PATH)
    proj = storage.load_project(PROJECT_ID)
    if not proj:
        print(f"Project {PROJECT_ID} not found", file=sys.stderr)
        return 1

    board = proj.get("board_state") or {}
    if not isinstance(board, dict):
        print("Invalid board_state", file=sys.stderr)
        return 1

    lanes = list(board.keys())
    new_board = {lane: [] for lane in lanes}
    seen_mvp = {cid: False for cid, _, _ in MVP_CARDS}

    for lane, tasks in board.items():
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            task = dict(task)
            if tid in CHURN_IDS:
                task["blockedReason"] = "Chronic churn — triaged for MVP baseline"
                new_board.setdefault("Blocked", []).append(task)
                continue
            if tid in seen_mvp:
                seen_mvp[tid] = True
                target = next(l for c, _, l in MVP_CARDS if c == tid)
                new_board.setdefault(target, []).append(task)
                continue
            if lane in ("In Progress", "Needs User", "Needs PO", "Blocked", "Backlog"):
                meta = dict(task.get("meta") or {})
                meta["mvpFrozen"] = True
                task["meta"] = meta
                _strip_freeze_latch_flags(task)
                new_board.setdefault("Features", []).append(task)
            else:
                new_board.setdefault(lane, []).append(task)

    for card_id, title, lane in MVP_CARDS:
        if seen_mvp[card_id]:
            continue
        new_board.setdefault(lane, []).append(
            {
                "id": card_id,
                "title": title,
                "description": f"{title}. {FREEZE_NOTE}",
                "priority": 1,
                "acceptanceCriteria": [
                    "flutter test test/meal_import_test.dart passes",
                ],
                "meta": {"mvpLastRun": True},
            }
        )

    storage.save_project(
        PROJECT_ID,
        proj["name"],
        proj["brief"],
        proj["workspace_dir"],
        new_board,
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
    print(f"Board triaged at {datetime.utcnow().isoformat()}Z")
    for lane, tasks in sorted(new_board.items()):
        if tasks:
            print(f"  {lane}: {len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
