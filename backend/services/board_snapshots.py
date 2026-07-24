"""Rolling board snapshots so wiped cards can be restored without SQL."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import ensure_allhands_home

MAX_SNAPSHOTS_PER_PROJECT = 10


def board_snapshots_dir(project_id: str) -> Path:
    path = ensure_allhands_home() / "board_snapshots" / str(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _count_tasks(board: Any) -> int:
    if not isinstance(board, dict):
        return 0
    total = 0
    for tasks in board.values():
        if isinstance(tasks, list):
            total += len(tasks)
    return total


def write_board_snapshot(
    project_id: str,
    board_state: Dict[str, Any],
    *,
    project_name: str = "",
) -> Optional[Path]:
    """Write a snapshot JSON; keep the last MAX_SNAPSHOTS_PER_PROJECT files."""
    if not project_id:
        return None
    # Skip empty boards unless we want a clear marker — still useful after clear.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = board_snapshots_dir(project_id) / f"board-{stamp}.json"
    payload = {
        "projectId": project_id,
        "projectName": project_name,
        "savedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCount": _count_tasks(board_state),
        "board_state": board_state,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _prune_snapshots(project_id)
    return path


def _prune_snapshots(project_id: str) -> None:
    files = sorted(
        board_snapshots_dir(project_id).glob("board-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[MAX_SNAPSHOTS_PER_PROJECT:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_board_snapshots(project_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(
        board_snapshots_dir(project_id).glob("board-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": path.stem,
                "filename": path.name,
                "savedAt": data.get("savedAt") or datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                "taskCount": int(data.get("taskCount") or _count_tasks(data.get("board_state"))),
                "projectName": data.get("projectName") or "",
            }
        )
    return items


def load_board_snapshot(project_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """Load snapshot by id (stem) or filename; returns full payload."""
    safe = re.sub(r"[^\w\-]", "", snapshot_id.replace(".json", ""))
    if not safe:
        return None
    directory = board_snapshots_dir(project_id)
    candidates = [
        directory / f"{safe}.json",
        directory / f"board-{safe}.json" if not safe.startswith("board-") else directory / f"{safe}.json",
    ]
    # Also allow exact stem match from list_board_snapshots
    for path in directory.glob("board-*.json"):
        if path.stem == snapshot_id or path.name == snapshot_id or path.stem == safe:
            candidates.insert(0, path)
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None
