"""Rolling board snapshots so wiped cards can be restored without SQL."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import ensure_allhands_home

MAX_NONEMPTY_SNAPSHOTS = 18
MAX_EMPTY_SNAPSHOTS = 2
SNAPSHOT_COOLDOWN_SEC = 180

_last_write_at: Dict[str, float] = {}


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


def _board_fingerprint(board_state: Any) -> str:
    try:
        raw = json.dumps(board_state, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(board_state)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _latest_snapshot_payload(project_id: str) -> Optional[Dict[str, Any]]:
    files = sorted(
        board_snapshots_dir(project_id).glob("board-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _should_write(
    project_id: str,
    board_state: Dict[str, Any],
    *,
    force: bool,
) -> bool:
    if force:
        last = _latest_snapshot_payload(project_id)
        if last and _board_fingerprint(last.get("board_state")) == _board_fingerprint(board_state):
            return False
        return True
    last = _latest_snapshot_payload(project_id)
    if last and _board_fingerprint(last.get("board_state")) == _board_fingerprint(board_state):
        return False
    last_count = int(last.get("taskCount") or 0) if last else None
    count = _count_tasks(board_state)
    if last_count is None or last_count != count:
        return True
    prev = _last_write_at.get(project_id) or 0.0
    return (time.monotonic() - prev) >= SNAPSHOT_COOLDOWN_SEC


def write_board_snapshot(
    project_id: str,
    board_state: Dict[str, Any],
    *,
    project_name: str = "",
    force: bool = False,
) -> Optional[Path]:
    """Write a snapshot JSON when the board changed, the cooldown elapsed, or force=True."""
    if not project_id:
        return None
    if not _should_write(project_id, board_state, force=force):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = board_snapshots_dir(project_id) / f"board-{stamp}.json"
    payload = {
        "projectId": project_id,
        "projectName": project_name,
        "savedAt": datetime.now().isoformat(timespec="milliseconds"),
        "taskCount": _count_tasks(board_state),
        "board_state": board_state,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _last_write_at[project_id] = time.monotonic()
    _prune_snapshots(project_id)
    return path


def _snapshot_task_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("taskCount") or _count_tasks(data.get("board_state")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _prune_snapshots(project_id: str) -> None:
    files = sorted(
        board_snapshots_dir(project_id).glob("board-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    nonempty: List[Path] = []
    empty: List[Path] = []
    for path in files:
        if _snapshot_task_count(path) > 0:
            nonempty.append(path)
        else:
            empty.append(path)
    keep: set[Path] = set(nonempty[:MAX_NONEMPTY_SNAPSHOTS])
    keep.update(empty[:MAX_EMPTY_SNAPSHOTS])
    if nonempty:
        richest = max(nonempty, key=lambda p: (_snapshot_task_count(p), p.stat().st_mtime))
        keep.add(richest)
    for old in files:
        if old in keep:
            continue
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
