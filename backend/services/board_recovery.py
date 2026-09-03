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


def _lane_counts(board: Any) -> Dict[str, int]:
    if not isinstance(board, dict):
        return {}
    out: Dict[str, int] = {}
    for lane, tasks in board.items():
        if isinstance(tasks, list):
            out[str(lane)] = len(tasks)
    return out


def scan_board_recovery_options(project_id: str, project_name: str = "") -> Dict[str, Any]:
    """Compare live DB, legacy DB, and snapshots for richer board copies."""
    live_db = allhands_home() / "scrum_memory.db"
    live_projects = _read_projects_from_db(live_db)
    legacy_projects = _read_projects_from_db(LEGACY_DB_PATH)

    live = next((p for p in live_projects if p["id"] == project_id), None)
    live_count = int(live["taskCount"]) if live else 0
    live_lanes = _lane_counts(live["board_state"]) if live else {}

    candidates: List[Dict[str, Any]] = []

    for snap in list_board_snapshots(project_id):
        if snap.get("taskCount", 0) > live_count:
            snap_board = None
            try:
                loaded = load_board_snapshot(project_id, str(snap["id"]))
                if isinstance(loaded, dict):
                    snap_board = loaded.get("board_state") if "board_state" in loaded else loaded
            except Exception:
                snap_board = None
            candidates.append(
                {
                    "kind": "snapshot",
                    "id": snap["id"],
                    "label": f"Snapshot {snap.get('savedAt')} ({snap.get('taskCount')} cards)",
                    "taskCount": snap.get("taskCount", 0),
                    "source": snap.get("filename"),
                    "laneCounts": _lane_counts(snap_board) if snap_board else {},
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
                    "laneCounts": _lane_counts(proj.get("board_state")),
                }
            )

    return {
        "projectId": project_id,
        "liveTaskCount": live_count,
        "liveLaneCounts": live_lanes,
        "liveDb": str(live_db),
        "legacyDbExists": LEGACY_DB_PATH.is_file(),
        "candidates": sorted(candidates, key=lambda c: int(c.get("taskCount") or 0), reverse=True),
        "orphanProjects": list_orphan_snapshot_projects(),
    }


def list_orphan_snapshot_projects() -> List[Dict[str, Any]]:
    """Snapshot folders whose project row is gone from the live DB."""
    from backend import state
    from backend.services.board_snapshots import list_board_snapshots

    root = allhands_home() / "board_snapshots"
    if not root.is_dir():
        return []
    live_ids = {str(p.get("id") or "") for p in state.storage.list_projects()}
    out: List[Dict[str, Any]] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        pid = folder.name
        if not pid or pid in live_ids:
            continue
        snaps = list_board_snapshots(pid)
        if not snaps:
            continue
        best = max(snaps, key=lambda s: int(s.get("taskCount") or 0))
        count = int(best.get("taskCount") or 0)
        if count <= 0:
            continue
        name = str(best.get("projectName") or pid)
        out.append(
            {
                "kind": "orphan_snapshot",
                "id": pid,
                "snapshotId": best.get("id"),
                "label": f"Deleted project '{name}' ({count} cards)",
                "taskCount": count,
                "source": best.get("filename"),
                "projectName": name,
            }
        )
    return sorted(out, key=lambda c: int(c.get("taskCount") or 0), reverse=True)


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

    if kind == "orphan_snapshot":
        from backend.services.board_snapshots import list_board_snapshots

        snaps = list_board_snapshots(source_id)
        if not snaps:
            return None, "No snapshots for deleted project"
        best = max(snaps, key=lambda s: int(s.get("taskCount") or 0))
        payload = load_board_snapshot(source_id, str(best.get("id") or ""))
        if not payload or not isinstance(payload.get("board_state"), dict):
            return None, "Orphan snapshot not found"
        name = str(payload.get("projectName") or best.get("projectName") or source_id)
        return payload["board_state"], f"Restored deleted project '{name}' from snapshot"

    return None, f"Unknown recovery kind: {kind}"


def recreate_project_from_orphan(orphan_id: str, board: Dict[str, Any]) -> str:
    """Insert a projects row for a snapshot folder that has no DB row."""
    from backend import state
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.config import DEFAULT_VIRTUAL_FS
    from backend.services.board_snapshots import list_board_snapshots

    existing = {str(p.get("id") or "") for p in state.storage.list_projects()}
    snaps = list_board_snapshots(orphan_id)
    best = max(snaps, key=lambda s: int(s.get("taskCount") or 0)) if snaps else {}
    payload = load_board_snapshot(orphan_id, str(best.get("id") or "")) if best else {}
    name = str((payload or {}).get("projectName") or "Recovered project")
    if orphan_id in existing:
        return name
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "recovered"
    short = orphan_id.replace("-", "")[:8] or "proj"
    candidate = f"./{slug}-recovered-{short}"
    state.storage.save_project(
        orphan_id,
        name,
        "",
        candidate,
        board,
        dict(DEFAULT_VIRTUAL_FS),
        list(agent_po.assigned_skills),
        list(agent_dev.assigned_skills),
        list(agent_cr.assigned_skills),
        list(agent_qa.assigned_skills),
        agent_po.model,
        agent_dev.model,
        agent_cr.model,
        agent_qa.model,
        persist_board=True,
        force_board=True,
    )
    return name
