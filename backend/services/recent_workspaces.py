"""VS Code-style recent workspace folders under ALLHANDS_HOME."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import ensure_allhands_home

RECENTS_FILENAME = "recent_workspaces.json"
MAX_RECENTS = 20


def recents_path() -> Path:
    return ensure_allhands_home() / RECENTS_FILENAME


def load_recents() -> List[Dict[str, Any]]:
    path = recents_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("workspaces") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("projectId") or item.get("id") or "").strip()
        workspace = str(item.get("path") or item.get("workspaceDir") or "").strip()
        if not pid or not workspace:
            continue
        out.append(
            {
                "projectId": pid,
                "id": pid,
                "name": str(item.get("name") or pid),
                "path": workspace,
                "workspaceDir": workspace,
                "lastOpened": str(item.get("lastOpened") or ""),
            }
        )
    return out


def save_recents(items: List[Dict[str, Any]]) -> None:
    trimmed = items[:MAX_RECENTS]
    payload = {
        "workspaces": [
            {
                "projectId": str(item.get("projectId") or item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or item.get("workspaceDir") or ""),
                "lastOpened": str(item.get("lastOpened") or ""),
            }
            for item in trimmed
            if str(item.get("projectId") or item.get("id") or "").strip()
            and str(item.get("path") or item.get("workspaceDir") or "").strip()
        ]
    }
    path = recents_path()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def touch_recent(*, project_id: str, name: str, workspace_dir: str) -> None:
    pid = str(project_id or "").strip()
    workspace = str(workspace_dir or "").strip()
    if not pid or not workspace:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = [item for item in load_recents() if str(item.get("projectId")) != pid]
    items.insert(
        0,
        {
            "projectId": pid,
            "id": pid,
            "name": str(name or pid),
            "path": workspace,
            "workspaceDir": workspace,
            "lastOpened": now,
        },
    )
    save_recents(items)


def remove_recent(project_id: str) -> bool:
    pid = str(project_id or "").strip()
    items = load_recents()
    next_items = [item for item in items if str(item.get("projectId")) != pid]
    if len(next_items) == len(items):
        return False
    save_recents(next_items)
    return True


def find_recent(project_id: str) -> Optional[Dict[str, Any]]:
    pid = str(project_id or "").strip()
    for item in load_recents():
        if str(item.get("projectId")) == pid:
            return item
    return None


def seed_recents_from_storage() -> None:
    """If recents are empty, copy SQLite projects whose workspace folder still exists."""
    if load_recents():
        return
    from backend import state

    rows = state.storage.list_projects()
    seeded: List[Dict[str, Any]] = []
    for row in rows:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        proj = state.storage.load_project(pid)
        workspace = str((proj or {}).get("workspace_dir") or "").strip()
        if not workspace or not Path(workspace).expanduser().is_dir():
            continue
        seeded.append(
            {
                "projectId": pid,
                "id": pid,
                "name": str((proj or {}).get("name") or row.get("name") or pid),
                "path": workspace,
                "workspaceDir": workspace,
                "lastOpened": str(row.get("updated_at") or ""),
            }
        )
    if seeded:
        save_recents(seeded)


def projects_list_for_client() -> List[Dict[str, Any]]:
    """Dropdown source: recents first, then leftover SQLite rows with a live folder."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in load_recents():
        pid = str(item.get("projectId") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(
            {
                "id": pid,
                "name": str(item.get("name") or pid),
                "workspaceDir": str(item.get("path") or ""),
                "updated_at": str(item.get("lastOpened") or ""),
            }
        )
    from backend import state

    for row in state.storage.list_projects():
        pid = str(row.get("id") or "")
        if not pid or pid in seen:
            continue
        proj = state.storage.load_project(pid)
        workspace = str((proj or {}).get("workspace_dir") or "").strip()
        if workspace and not Path(workspace).expanduser().is_dir():
            continue
        seen.add(pid)
        out.append(
            {
                "id": pid,
                "name": str(row.get("name") or pid),
                "workspaceDir": workspace,
                "updated_at": str(row.get("updated_at") or ""),
            }
        )
    return out
