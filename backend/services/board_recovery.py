"""Recover board_state from snapshots or a legacy scrum_memory.db."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import LEGACY_DB_PATH, allhands_home
from backend.services.board_snapshots import list_board_snapshots, load_board_snapshot


def _count_tasks(board: Any) -> int:
    if not isinstance(board, dict):
        return 0
    return sum(len(v) for v in board.values() if isinstance(v, list))


def _read_projects_from_db(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, board_state, po_model, dev_model, cr_model, qa_model FROM projects"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            board = json.loads(row["board_state"] or "{}")
        except json.JSONDecodeError:
            board = {}
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "taskCount": _count_tasks(board),
                "board_state": board,
                "po_model": row["po_model"],
                "dev_model": row["dev_model"],
                "cr_model": row["cr_model"],
                "qa_model": row["qa_model"],
                "source": str(db_path),
            }
        )
    return out


def scan_board_recovery_options(project_id: str, project_name: str = "") -> Dict[str, Any]:
    """Compare live DB, legacy DB, and snapshots for richer board copies."""
    live_db = allhands_home() / "scrum_memory.db"
    live_projects = _read_projects_from_db(live_db)
    legacy_projects = _read_projects_from_db(LEGACY_DB_PATH)

    live = next((p for p in live_projects if p["id"] == project_id), None)
    live_count = int(live["taskCount"]) if live else 0

    candidates: List[Dict[str, Any]] = []

    for snap in list_board_snapshots(project_id):
        if snap.get("taskCount", 0) > live_count:
            candidates.append(
                {
                    "kind": "snapshot",
                    "id": snap["id"],
                    "label": f"Snapshot {snap.get('savedAt')} ({snap.get('taskCount')} cards)",
                    "taskCount": snap.get("taskCount", 0),
                    "source": snap.get("filename"),
                }
            )

    name_key = (project_name or (live or {}).get("name") or "").strip().lower()
    for proj in legacy_projects:
        same_id = proj["id"] == project_id
        same_name = name_key and str(proj.get("name") or "").strip().lower() == name_key
        if (same_id or same_name) and proj["taskCount"] > live_count:
            candidates.append(
                {
                    "kind": "legacy",
                    "id": proj["id"],
                    "label": f"Legacy DB '{proj['name']}' ({proj['taskCount']} cards)",
                    "taskCount": proj["taskCount"],
                    "source": proj["source"],
                    "legacyProjectId": proj["id"],
                }
            )

    return {
        "projectId": project_id,
        "liveTaskCount": live_count,
        "liveDb": str(live_db),
        "legacyDbExists": LEGACY_DB_PATH.is_file(),
        "candidates": sorted(candidates, key=lambda c: int(c.get("taskCount") or 0), reverse=True),
    }


def load_recovery_board(
    project_id: str,
    *,
    kind: str,
    source_id: str,
    project_name: str = "",
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (board_state, message)."""
    if kind == "snapshot":
        payload = load_board_snapshot(project_id, source_id)
        if not payload or not isinstance(payload.get("board_state"), dict):
            return None, "Snapshot not found"
        return payload["board_state"], f"Restored board from snapshot {source_id}"

    if kind == "legacy":
        legacy_projects = _read_projects_from_db(LEGACY_DB_PATH)
        match = next((p for p in legacy_projects if p["id"] == source_id), None)
        if not match:
            name_key = project_name.strip().lower()
            match = next(
                (
                    p
                    for p in legacy_projects
                    if name_key and str(p.get("name") or "").strip().lower() == name_key
                ),
                None,
            )
        if not match or not isinstance(match.get("board_state"), dict):
            return None, "Legacy project board not found"
        return match["board_state"], f"Restored board from legacy DB '{match.get('name')}'"

    return None, f"Unknown recovery kind: {kind}"
