#!/usr/bin/env python3
"""Split visit-capped Shopping list + Settings cards (meal planner / 85d8c5e9)."""
from __future__ import annotations

import re
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ID = "85d8c5e9-a905-4868-a773-29eb4bfa436d"
SHOPPING_PARENT = "TASK-7CE2FA733F1E49F2827D95474DDB4CD2"
SETTINGS_PARENT = "TASK-734F1C129C5C44069BB767DDBC5BEA14"
TRIAGE_NOTE = "Split after Developer visit cap — split_shopping_settings_visit_cap_85d8c5e9.py"

SHOPPING_CHILDREN = (
    (
        "Shopping list — screen shell and route",
        "Create ShoppingListScreen scaffold under lib/screens/: app bar, body placeholder, "
        "route registration from main navigation. Match existing theme.",
        [
            "Shopping list route opens from main navigation",
            "flutter analyze passes on new screen files",
        ],
    ),
    (
        "Shopping list — provider and aggregation",
        "Riverpod provider that aggregates ingredients/items from meal plans or meals into "
        "a deduplicated shopping list model. Offline-only; use existing Drift tables.",
        [
            "Provider exposes aggregated list without network",
            "Duplicate ingredients merge with quantity sum where applicable",
        ],
    ),
    (
        "Shopping list — list UI and empty state",
        "ListView (or similar) bound to shopping list provider. Loading and empty states. "
        "Basic check-off interaction optional if trivial.",
        [
            "Empty state when no items to buy",
            "List renders items from provider",
        ],
    ),
)

SETTINGS_CHILDREN = (
    (
        "Settings — Riverpod provider shell",
        "Create lib/providers/settings_provider.dart with a SettingsNotifier/AsyncNotifier "
        "skeleton and types for theme mode and default store id.",
        [
            "Provider compiles and is wired for override in main",
            "No analyzer errors in settings provider file",
        ],
    ),
    (
        "Settings — theme mode persistence",
        "Persist theme (light/dark/system) via shared_preferences or existing persistence "
        "pattern in the project. Expose themeMode on settings state.",
        [
            "Theme choice survives app restart",
            "MaterialApp themeMode reads from provider",
        ],
    ),
    (
        "Settings — default store selection",
        "Load/save default store from local DB or preferences. Expose on settings state "
        "for shopping/aisle features later.",
        [
            "Default store id available from provider",
            "Invalid/missing store handled without crash",
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
    "lastCircuitExitReason",
    "circuitBreakerPoRetryUsed",
    "circuitBreakerParked",
    "splitAttemptedOnStuck",
    "visitCapSplitRetried",
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


def _split_parent(
    board: dict,
    parent_id: str,
    children: tuple,
    *,
    label: str,
) -> list[str]:
    located = _find_task(board, parent_id)
    if not located:
        print(f"  {label}: parent {parent_id} not on board — skip", file=sys.stderr)
        return []
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
    for title, description, ac in children:
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
                "splitFromTaskId": parent_id,
                "meta": {"visitCapSplitChild": True},
            }
        )
        created.append(tid)

    print(f"  {label}: {parent_id} {lane} → Done; {len(created)} Backlog children")
    for tid, (title, _, _) in zip(created, children):
        print(f"    {tid}: {title}")
    return created


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

    _split_parent(board, SHOPPING_PARENT, SHOPPING_CHILDREN, label="Shopping list")
    _split_parent(board, SETTINGS_PARENT, SETTINGS_CHILDREN, label="Settings")

    in_progress = board.get("In Progress") or []
    if isinstance(in_progress, list):
        drop = {SHOPPING_PARENT, SETTINGS_PARENT}
        board["In Progress"] = [
            t for t in in_progress if str(t.get("id") or "") not in drop
        ]

    for lane in ("In Progress", "Needs PO", "Blocked", "Needs User", "Backlog"):
        for task in board.get(lane) or []:
            if isinstance(task, dict):
                _strip_latch(task)

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
