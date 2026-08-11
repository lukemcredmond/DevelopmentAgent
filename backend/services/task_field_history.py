"""Per-card field revision history (title, description, AC, SDD snapshot)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

TRACKED_FIELDS = ("title", "description", "acceptanceCriteria", "sdd")
SDD_KEYS = ("userStory", "scope", "outOfScope", "testPlan")
MAX_PER_FIELD = 40
PREVIEW_CHARS = 120


def _normalize_ac(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(c).strip() for c in value if str(c).strip()]


def serialize_field_value(field: str, value: Any) -> str:
    if field == "acceptanceCriteria":
        return json.dumps(_normalize_ac(value), ensure_ascii=False)
    if field == "sdd":
        if isinstance(value, dict):
            payload = {k: str(value.get(k) or "").strip() for k in SDD_KEYS}
        else:
            payload = {k: "" for k in SDD_KEYS}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(value or "").strip()


def sdd_snapshot_from_task(task: Dict[str, Any]) -> Dict[str, str]:
    return {k: str(task.get(k) or "").strip() for k in SDD_KEYS}


def preview_value(value: str, *, limit: int = PREVIEW_CHARS) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def list_entry_public(row: Dict[str, Any]) -> Dict[str, Any]:
    value = str(row.get("value") or "")
    return {
        "id": row.get("id"),
        "field": row.get("field"),
        "timestamp": row.get("timestamp"),
        "source": row.get("source") or "user",
        "preview": preview_value(value),
    }


def record_task_field_change(
    task_id: str,
    field: str,
    new_value: Any,
    *,
    old_value: Any = None,
    source: str = "user",
    project_id: Optional[str] = None,
) -> Optional[str]:
    """
    Persist a field snapshot when the serialized value changed.
    Returns entry id or None if skipped.
    """
    if field not in TRACKED_FIELDS:
        return None
    from backend import state

    pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
    storage = getattr(state, "storage", None)
    if storage is None or not hasattr(storage, "add_task_field_changelog"):
        return None

    new_ser = serialize_field_value(field, new_value)
    if old_value is not None:
        old_ser = serialize_field_value(field, old_value)
        if old_ser == new_ser:
            return None
    else:
        # Compare against latest stored value
        recent = storage.get_task_field_changelog(pid, task_id, field, limit=1)
        if recent and str(recent[0].get("value") or "") == new_ser:
            return None

    # Preserve pre-change value as first history row when this is the first edit.
    if old_value is not None:
        old_ser = serialize_field_value(field, old_value)
        recent = storage.get_task_field_changelog(pid, task_id, field, limit=1)
        if not recent and old_ser and old_ser not in ("[]",):
            if field != "sdd" or any(
                str(v).strip() for v in json.loads(old_ser).values()
            ):
                storage.add_task_field_changelog(
                    pid,
                    task_id,
                    field,
                    old_ser,
                    source="baseline",
                    max_per_field=MAX_PER_FIELD,
                )

    return storage.add_task_field_changelog(
        pid,
        task_id,
        field,
        new_ser,
        source=source or "user",
        max_per_field=MAX_PER_FIELD,
    )


def record_task_fields_from_update(
    task: Dict[str, Any],
    *,
    before: Dict[str, Any],
    source: str = "user",
    changed_keys: Optional[Sequence[str]] = None,
) -> List[str]:
    """Record title/description/AC/SDD diffs after a task mutation."""
    tid = str(task.get("id") or "")
    if not tid:
        return []
    keys = set(changed_keys) if changed_keys is not None else {
        "title",
        "description",
        "acceptanceCriteria",
        *SDD_KEYS,
    }
    entry_ids: List[str] = []

    if "title" in keys:
        eid = record_task_field_change(
            tid,
            "title",
            task.get("title"),
            old_value=before.get("title"),
            source=source,
        )
        if eid:
            entry_ids.append(eid)

    if "description" in keys:
        eid = record_task_field_change(
            tid,
            "description",
            task.get("description"),
            old_value=before.get("description"),
            source=source,
        )
        if eid:
            entry_ids.append(eid)

    if "acceptanceCriteria" in keys:
        eid = record_task_field_change(
            tid,
            "acceptanceCriteria",
            task.get("acceptanceCriteria"),
            old_value=before.get("acceptanceCriteria"),
            source=source,
        )
        if eid:
            entry_ids.append(eid)

    if keys.intersection(SDD_KEYS):
        eid = record_task_field_change(
            tid,
            "sdd",
            sdd_snapshot_from_task(task),
            old_value=sdd_snapshot_from_task(before),
            source=source,
        )
        if eid:
            entry_ids.append(eid)

    return entry_ids


def ensure_baseline_snapshot(
    task: Dict[str, Any],
    field: str,
    *,
    source: str = "baseline",
) -> Optional[str]:
    """If history empty and field non-empty, record current value once."""
    if field not in TRACKED_FIELDS:
        return None
    from backend import state

    tid = str(task.get("id") or "")
    if not tid:
        return None
    pid = state.CURRENT_PROJECT_ID or "default-proj"
    storage = getattr(state, "storage", None)
    if storage is None:
        return None
    existing = storage.get_task_field_changelog(pid, tid, field, limit=1)
    if existing:
        return None
    if field == "sdd":
        value: Any = sdd_snapshot_from_task(task)
    elif field == "acceptanceCriteria":
        value = task.get("acceptanceCriteria")
    else:
        value = task.get(field)
    ser = serialize_field_value(field, value)
    if not ser or ser in ("[]", "{}", '""'):
        # empty AC / empty SDD / blank title
        if field in ("title", "description") and not ser:
            return None
        if field == "acceptanceCriteria" and ser == "[]":
            return None
        if field == "sdd":
            payload = json.loads(ser) if ser else {}
            if not any(str(v).strip() for v in payload.values()):
                return None
    return record_task_field_change(tid, field, value, source=source)


def list_field_history(
    task_id: str,
    field: str,
    *,
    limit: int = 40,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from backend import state

    if field not in TRACKED_FIELDS:
        return []
    pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
    storage = state.storage
    rows = storage.get_task_field_changelog(pid, task_id, field, limit=limit)
    return [list_entry_public(r) for r in rows]


def get_field_history_entry(
    entry_id: str,
    *,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    from backend import state

    pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
    row = state.storage.get_task_field_changelog_entry(pid, entry_id)
    if not row:
        return None
    return {
        "id": row.get("id"),
        "taskId": row.get("task_id"),
        "field": row.get("field"),
        "value": row.get("value"),
        "source": row.get("source"),
        "timestamp": row.get("timestamp"),
    }
