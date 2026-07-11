"""Feature parents — stationary lane cards with history and child implementation tasks."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import (
    all_task_ids,
    find_task_by_id,
    init_new_task,
    normalize_task,
    record_task_decision,
)
from backend.services.board_lanes import FEATURES_LANE, normalize_board_lanes
from backend.services.board_service import append_backlog_tasks, publish_board_update
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state

MAX_FEATURE_HISTORY = 50
MAX_HISTORY_EXCERPT = 400


def generate_feature_id() -> str:
    return f"FEAT-{uuid.uuid4().hex.upper()}"


def is_feature_task(task: Dict[str, Any]) -> bool:
    normalize_task(task)
    return str(task.get("workType") or "") == "feature"


def list_features() -> List[Dict[str, Any]]:
    normalize_board_lanes(state.SHARED_BOARD)
    features: List[Dict[str, Any]] = []
    for task in state.SHARED_BOARD.get(FEATURES_LANE, []):
        normalize_task(task)
        if is_feature_task(task):
            features.append(task)
    return features


def find_feature_by_id(feature_id: str) -> Optional[Dict[str, Any]]:
    feature = find_task_by_id(str(feature_id))
    if feature and is_feature_task(feature):
        return feature
    return None


def _now_ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp": str(entry.get("timestamp") or _now_ts()),
        "source": str(entry.get("source") or "user"),
        "requestTitle": str(entry.get("requestTitle") or ""),
        "requestBody": str(entry.get("requestBody") or ""),
        "poSummary": str(entry.get("poSummary") or ""),
        "childTaskId": str(entry.get("childTaskId") or ""),
    }


def record_feature_decision(
    feature_id: str,
    agent: str,
    decision_type: str,
    summary: str,
    detail: str = "",
) -> None:
    record_task_decision(feature_id, agent, decision_type, summary, detail)


def append_feature_history(feature_id: str, entry: Dict[str, Any]) -> None:
    feature = find_feature_by_id(feature_id)
    if not feature:
        return
    normalize_task(feature)
    history = list(feature.get("featureHistory") or [])
    history.append(_normalize_history_entry(entry))
    if len(history) > MAX_FEATURE_HISTORY:
        history = history[-MAX_FEATURE_HISTORY:]
    feature["featureHistory"] = history


def _init_feature_parent(
    title: str,
    description: str,
    *,
    feature_id: Optional[str] = None,
) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": feature_id or generate_feature_id(),
        "title": title,
        "description": description,
        "status": FEATURES_LANE,
        "workType": "feature",
        "requiresDev": False,
        "requiresQa": False,
        "createdBy": "user",
        "featureHistory": [],
        "childTaskIds": [],
        "acceptanceCriteria": [],
        "blockedBy": [],
    }
    init_new_task(feature)
    feature["status"] = FEATURES_LANE
    feature["workType"] = "feature"
    feature["requiresDev"] = False
    feature["requiresQa"] = False
    return feature


def _place_feature(feature: Dict[str, Any]) -> None:
    normalize_board_lanes(state.SHARED_BOARD)
    state.SHARED_BOARD.setdefault(FEATURES_LANE, [])
    state.SHARED_BOARD[FEATURES_LANE].append(feature)


def _spawn_child_task(feature_id: str, child_raw: Dict[str, Any]) -> Dict[str, Any]:
    before = all_task_ids()
    child_payload = dict(child_raw)
    child_payload["featureId"] = feature_id
    child_payload.setdefault("requiresDev", True)
    child_payload.setdefault("requiresQa", True)
    child_payload.setdefault("workType", "implementation")
    append_backlog_tasks([child_payload])
    after = all_task_ids()
    new_ids = after - before
    for tid in new_ids:
        child = find_task_by_id(tid)
        if child and str(child.get("featureId") or "") == feature_id:
            return child
    for tid in new_ids:
        child = find_task_by_id(tid)
        if child:
            return child
    return {}


def _link_child_to_feature(feature: Dict[str, Any], child: Dict[str, Any]) -> str:
    normalize_task(feature)
    normalize_task(child)
    child_id = str(child.get("id", ""))
    child_ids = list(feature.get("childTaskIds") or [])
    if child_id and child_id not in child_ids:
        child_ids.append(child_id)
    feature["childTaskIds"] = child_ids
    child["featureId"] = str(feature.get("id", ""))
    return child_id


def create_feature(
    title: str,
    description: str,
    *,
    request_title: str,
    request_body: str,
    child_task: Dict[str, Any],
    po_summary: str = "",
    source: str = "user",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Create a feature parent in Features lane and spawn a child backlog card."""
    feature = _init_feature_parent(title, description)
    _place_feature(feature)
    child = _spawn_child_task(str(feature["id"]), child_task)
    child_id = _link_child_to_feature(feature, child)
    append_feature_history(
        str(feature["id"]),
        {
            "source": source,
            "requestTitle": request_title,
            "requestBody": request_body,
            "poSummary": po_summary or "Initial feature request",
            "childTaskId": child_id,
        },
    )
    record_feature_decision(
        str(feature["id"]),
        "Product Owner",
        "feature_intake",
        f"Created feature '{title}'",
        f"Spawned child task {child_id}",
    )
    save_current_project_state()
    publish_board_update(str(feature["id"]), source="feature_create")
    return feature, child


def update_feature(
    feature_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    request_title: str,
    request_body: str,
    child_task: Dict[str, Any],
    po_summary: str = "",
    source: str = "user",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Update an existing feature parent and spawn a new child backlog card."""
    feature = find_feature_by_id(feature_id)
    if not feature:
        raise ValueError(f"Feature not found: {feature_id}")
    normalize_task(feature)
    if title:
        feature["title"] = title
    if description:
        feature["description"] = description
    child = _spawn_child_task(feature_id, child_task)
    child_id = _link_child_to_feature(feature, child)
    append_feature_history(
        feature_id,
        {
            "source": source,
            "requestTitle": request_title,
            "requestBody": request_body,
            "poSummary": po_summary or "Feature update",
            "childTaskId": child_id,
        },
    )
    record_feature_decision(
        feature_id,
        "Product Owner",
        "feature_intake",
        f"Updated feature '{feature.get('title', feature_id)}'",
        po_summary or f"Spawned child task {child_id}",
    )
    save_current_project_state()
    publish_board_update(feature_id, source="feature_update")
    return feature, child


def build_feature_context_for_po(
    new_request: Dict[str, str],
    *,
    features: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Compact summary of existing features for PO classification."""
    pool = features if features is not None else list_features()
    if not pool:
        return "Existing features: (none)\n"
    lines = ["Existing features:"]
    for feat in pool:
        normalize_task(feat)
        fid = str(feat.get("id", ""))
        desc = str(feat.get("description") or "")[:MAX_HISTORY_EXCERPT]
        history = list(feat.get("featureHistory") or [])[-3:]
        lines.append(f"\n- {fid}: {feat.get('title', '?')}")
        lines.append(f"  Spec: {desc}")
        if history:
            lines.append("  Recent history:")
            for entry in history:
                if isinstance(entry, dict):
                    lines.append(
                        f"    - [{entry.get('timestamp', '?')}] "
                        f"{entry.get('requestTitle', '?')}: {str(entry.get('poSummary', ''))[:120]}"
                    )
    lines.append(
        f"\nNew request:\n{new_request.get('title', '?')}: {new_request.get('description', '')}"
    )
    return "\n".join(lines)


def build_feature_context_block(feature_id: str) -> str:
    """Feature history block for child task agent prompts."""
    feature = find_feature_by_id(feature_id)
    if not feature:
        return ""
    normalize_task(feature)
    lines = [
        f"=== FEATURE CONTEXT (parent {feature_id}) ===",
        f"Living spec: {str(feature.get('description') or '')[:2000]}",
    ]
    history = list(feature.get("featureHistory") or [])
    if history:
        lines.append("Prior requests & decisions:")
        for entry in history[-10:]:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"- [{entry.get('timestamp', '?')}] {entry.get('requestTitle', '?')} → "
                f"{str(entry.get('poSummary', ''))[:300]}"
                + (f" (child: {entry.get('childTaskId')})" if entry.get("childTaskId") else "")
            )
    decisions = list(feature.get("decisions") or [])[-5:]
    if decisions:
        lines.append("Recent feature decisions:")
        for d in decisions:
            if isinstance(d, dict):
                lines.append(
                    f"- [{d.get('timestamp', '?')}] {d.get('agent', '?')} "
                    f"({d.get('type', '?')}): {str(d.get('summary', ''))[:200]}"
                )
    lines.append("")
    return "\n".join(lines)


def rollup_child_to_feature(child_task_id: str) -> None:
    """When a child task completes, roll key outcomes into the parent feature history."""
    child = find_task_by_id(child_task_id)
    if not child:
        return
    normalize_task(child)
    feature_id = str(child.get("featureId") or "")
    if not feature_id:
        return
    feature = find_feature_by_id(feature_id)
    if not feature:
        return

    summary_parts: List[str] = []
    for decision in reversed(child.get("decisions") or []):
        if not isinstance(decision, dict):
            continue
        dtype = str(decision.get("type") or "")
        if dtype in ("completion", "qa", "qa_fail", "move", "review"):
            summary_parts.append(str(decision.get("summary") or ""))
        if len(summary_parts) >= 3:
            break
    summary = "; ".join(s for s in summary_parts if s) or f"Child {child_task_id} completed"

    append_feature_history(
        feature_id,
        {
            "source": "rollup",
            "requestTitle": str(child.get("title") or child_task_id),
            "requestBody": str(child.get("description") or "")[:500],
            "poSummary": summary,
            "childTaskId": child_task_id,
        },
    )
    record_feature_decision(
        feature_id,
        "System",
        "child_complete",
        f"Child '{child.get('title', child_task_id)}' reached Done",
        summary,
    )
    save_current_project_state()
    publish_board_update(feature_id, source="feature_rollup")


def intake_feature_offline(
    title: str,
    description: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Offline fallback: always create a new feature + child."""
    child_task = {
        "title": title,
        "description": description,
        "acceptanceCriteria": [description] if description else [title],
    }
    feature, child = create_feature(
        title,
        description,
        request_title=title,
        request_body=description,
        child_task=child_task,
        po_summary="Offline fallback — Ollama unavailable",
        source="offline",
    )
    add_system_log(
        "Product Owner",
        "warning",
        f"Offline intake — created feature {feature.get('id')} + child {child.get('id')}",
    )
    return feature, child


def parse_po_feature_intake(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize PO classification JSON into a consistent shape."""
    action = str(obj.get("action") or "new").lower()
    if action not in ("new", "update"):
        action = "new"
    child_raw = obj.get("childTask") if isinstance(obj.get("childTask"), dict) else {}
    return {
        "action": action,
        "featureId": str(obj.get("featureId") or "").strip() or None,
        "featureTitle": str(obj.get("featureTitle") or obj.get("title") or "").strip(),
        "featureDescription": str(obj.get("featureDescription") or obj.get("description") or "").strip(),
        "historySummary": str(obj.get("historySummary") or "").strip(),
        "childTask": {
            "title": str(child_raw.get("title") or obj.get("featureTitle") or "Implement feature"),
            "description": str(child_raw.get("description") or obj.get("featureDescription") or ""),
            "acceptanceCriteria": child_raw.get("acceptanceCriteria")
            if isinstance(child_raw.get("acceptanceCriteria"), list)
            else [],
        },
    }
