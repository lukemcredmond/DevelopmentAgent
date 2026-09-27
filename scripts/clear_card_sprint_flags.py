#!/usr/bin/env python3
"""Clear stale sprint flags and close MVP cards after successful flutter tests."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ID = "c6196c51-11ec-4349-93b4-2282563bdc20"
MVP_DONE_IDS = frozenset({"MVP-PERSIST-DB", "MVP-SHOPPING-UI"})
MVP_NOTE = (
    "Implemented in lib/meal_repository.dart, lib/main.dart, shopping_list_service.dart; "
    "verified by meal_import_test.dart."
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
    "cardToolFailures",
)


def clear_sprint_flags(task: dict) -> None:
    for key in SPRINT_FLAG_KEYS:
        task.pop(key, None)


def strip_frozen_latch_flags(task: dict) -> None:
    meta = task.get("meta") or {}
    if not meta.get("mvpFrozen"):
        return
    clear_sprint_flags(task)


def run_flutter_tests(workspace: str) -> bool:
    try:
        subprocess.run(
            ["flutter", "analyze"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["flutter", "test", "test/meal_import_test.dart"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"flutter verification failed: {exc}", file=sys.stderr)
        return False


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

    workspace = str(proj.get("workspace_dir") or "")
    if workspace and not run_flutter_tests(workspace):
        print("Skipping MVP → Done moves; fix workspace and re-run.", file=sys.stderr)
        return 2

    board = proj.get("board_state") or {}
    if not isinstance(board, dict):
        print("Invalid board_state", file=sys.stderr)
        return 1

    new_board: dict = {lane: [] for lane in board.keys()}
    moved_done = 0
    cleared = 0

    for lane, tasks in board.items():
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task = dict(task)
            tid = str(task.get("id") or "")
            if lane == "Features":
                strip_frozen_latch_flags(task)
                cleared += 1
            if tid in MVP_DONE_IDS:
                clear_sprint_flags(task)
                desc = str(task.get("description") or "")
                if MVP_NOTE not in desc:
                    task["description"] = f"{desc}\n\n{MVP_NOTE}".strip()
                new_board.setdefault("Done", []).append(task)
                moved_done += 1
                continue
            if lane == "Needs PO" and tid == "TASK-3169816AA95C469BB5A96E0859724D98":
                clear_sprint_flags(task)
            new_board.setdefault(lane, []).append(task)

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
    print(f"Board updated at {datetime.now(timezone.utc).isoformat()}")
    print(f"  MVP cards moved to Done: {moved_done}")
    print(f"  Features cards latch-stripped: {cleared}")
    for lane, tasks in sorted(new_board.items()):
        if tasks:
            print(f"  {lane}: {len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
