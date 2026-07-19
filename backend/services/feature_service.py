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
    from backend.services.feature_similarity import (
        apply_same_request_reuse,
        find_same_request_match,
        iter_board_tasks,
    )

    feature = find_feature_by_id(feature_id)
    child_payload = dict(child_raw)
    child_payload["featureId"] = feature_id
    child_payload.setdefault("requiresDev", True)
    child_payload.setdefault("requiresQa", True)
    child_payload.setdefault("workType", "implementation")

    # Prefer reusing an existing same-request child (or any board card)
    child_ids = set(str(c) for c in (feature.get("childTaskIds") or []) if feature) if feature else set()
    pool = iter_board_tasks()
    match_result = find_same_request_match(child_payload, pool=pool)
    if match_result:
        match, score, reasons = match_result
        apply_same_request_reuse(feature, match, score=score, reasons=reasons)
        if feature:
            _link_child_to_feature(feature, match)
        add_system_log(
            "Product Owner",
            "info",
            f"Feature {feature_id}: reused existing child {match.get('id')} "
            f"(score {score:.2f}) instead of spawning a duplicate",
        )
        return match

    before = all_task_ids()
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
    # append may have reused without creating — find via related on feature
    if feature:
        for rid in feature.get("relatedTaskIds") or []:
            child = find_task_by_id(str(rid))
            if child and str(child.get("workType") or "") != "feature":
                return child
        for cid in child_ids:
            child = find_task_by_id(cid)
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

    # Merge child files onto the feature (deduped by path)
    feature_files = list(feature.get("files") or [])
    seen_paths = set()
    for entry in feature_files:
        if isinstance(entry, dict) and entry.get("path"):
            seen_paths.add(str(entry["path"]))
        elif isinstance(entry, str):
            seen_paths.add(entry)
    for entry in child.get("files") or []:
        path = entry.get("path") if isinstance(entry, dict) else str(entry)
        if not path or path in seen_paths:
            continue
        seen_paths.add(str(path))
        feature_files.append(
            entry if isinstance(entry, dict) else {"path": path, "action": "touched"}
        )
    feature["files"] = feature_files[-40:]

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
    feature["featureRollup"] = build_feature_rollup(feature_id)
    save_current_project_state()
    publish_board_update(feature_id, source="feature_rollup")


def build_feature_rollup(feature_id: str) -> Dict[str, Any]:
    """Aggregate child status, files, and recent decisions for an epic hub view."""
    from backend.agents.task_context import get_task_lane

    feature = find_feature_by_id(feature_id)
    if not feature:
        return {"children": [], "files": [], "recentDecisions": []}

    normalize_task(feature)
    children_out: List[Dict[str, Any]] = []
    file_paths: List[str] = []
    seen_files: set[str] = set()
    decisions_out: List[Dict[str, Any]] = []

    def _add_file(path: str) -> None:
        if path and path not in seen_files:
            seen_files.add(path)
            file_paths.append(path)

    for entry in feature.get("files") or []:
        path = entry.get("path") if isinstance(entry, dict) else str(entry)
        _add_file(str(path) if path else "")

    for d in feature.get("decisions") or []:
        if isinstance(d, dict):
            decisions_out.append(
                {
                    "agent": str(d.get("agent") or ""),
                    "type": str(d.get("type") or ""),
                    "summary": str(d.get("summary") or "")[:300],
                    "timestamp": str(d.get("timestamp") or ""),
                    "childTaskId": "",
                    "childTitle": str(feature.get("title") or ""),
                }
            )

    for child_id in feature.get("childTaskIds") or []:
        cid = str(child_id)
        child = find_task_by_id(cid)
        if not child:
            children_out.append(
                {"id": cid, "title": "(missing)", "status": "?", "lane": "?"}
            )
            continue
        normalize_task(child)
        lane = get_task_lane(cid) or str(child.get("status") or "?")
        children_out.append(
            {
                "id": cid,
                "title": str(child.get("title") or cid),
                "status": str(child.get("status") or lane),
                "lane": lane,
            }
        )
        for entry in child.get("files") or []:
            path = entry.get("path") if isinstance(entry, dict) else str(entry)
            _add_file(str(path) if path else "")
        for d in (child.get("decisions") or [])[-5:]:
            if isinstance(d, dict):
                decisions_out.append(
                    {
                        "agent": str(d.get("agent") or ""),
                        "type": str(d.get("type") or ""),
                        "summary": str(d.get("summary") or "")[:300],
                        "timestamp": str(d.get("timestamp") or ""),
                        "childTaskId": cid,
                        "childTitle": str(child.get("title") or cid),
                    }
                )

    decisions_out.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return {
        "children": children_out,
        "files": file_paths[:40],
        "recentDecisions": decisions_out[:20],
    }


def _normalize_plan_child(raw: Dict[str, Any]) -> Dict[str, Any]:
    ac = raw.get("acceptanceCriteria")
    if not isinstance(ac, list):
        ac = raw.get("acceptance_criteria") if isinstance(raw.get("acceptance_criteria"), list) else []
    return {
        "title": str(raw.get("title") or "Untitled task").strip(),
        "description": str(raw.get("description") or "").strip(),
        "acceptanceCriteria": [str(c) for c in ac if c],
        "blockedBy": list(raw.get("blockedBy") or raw.get("blocked_by") or [])
        if isinstance(raw.get("blockedBy") or raw.get("blocked_by"), list)
        else [],
        "priority": raw.get("priority", 100),
        "workType": raw.get("workType") or raw.get("work_type") or "implementation",
        "requiresDev": raw.get("requiresDev", raw.get("requires_dev", True)),
        "requiresQa": raw.get("requiresQa", raw.get("requires_qa", True)),
    }


def _find_matching_feature(title: str, description: str) -> Optional[Dict[str, Any]]:
    from backend.services.feature_similarity import REUSE_THRESHOLD, score_task_similarity

    probe = {"title": title, "description": description}
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for feat in list_features():
        score, _ = score_task_similarity(probe, feat)
        if score >= REUSE_THRESHOLD and (best is None or score > best[0]):
            best = (score, feat)
    return best[1] if best else None


def apply_plan_epics_from_po_output(po_output: str) -> Dict[str, Any]:
    """Create Features-lane epics + child cards from PO plan JSON.

    Preferred shape:
      {"epics":[{"title","description","children":[{title,description,acceptanceCriteria,...}]}]}

    Fallback: flat JSON array → one synthetic epic "Project backlog".
    """
    import json
    import re

    summary: Dict[str, Any] = {
        "epicIds": [],
        "childIds": [],
        "reusedEpicIds": [],
        "epicCount": 0,
        "childCount": 0,
    }

    if not po_output or not str(po_output).strip():
        return summary

    epics_raw: List[Dict[str, Any]] = []

    if po_output == "SIMULATION_FALLBACK":
        epics_raw = [
            {
                "title": "Core scaffold",
                "description": "Primary module structure for the project.",
                "children": [
                    {
                        "title": "Create core scaffold",
                        "description": "Primary module structure.",
                        "acceptanceCriteria": ["Entry point runs"],
                    }
                ],
            },
            {
                "title": "Main feature",
                "description": "Deliver the brief capability.",
                "children": [
                    {
                        "title": "Implement main feature",
                        "description": "Deliver brief capability.",
                        "acceptanceCriteria": ["Feature works end-to-end"],
                    }
                ],
            },
        ]
    else:
        # Prefer object with epics[]
        obj = None
        bt = "```"
        for block in re.findall(rf"{bt}json\s*(.*?)\s*{bt}", po_output, re.DOTALL):
            try:
                parsed = json.loads(block.strip())
                if isinstance(parsed, dict):
                    obj = parsed
                    break
            except json.JSONDecodeError:
                continue
        if obj is None:
            try:
                parsed = json.loads(po_output.strip())
                if isinstance(parsed, dict):
                    obj = parsed
                elif isinstance(parsed, list):
                    epics_raw = [
                        {
                            "title": "Project backlog",
                            "description": "Stories from plan (legacy flat array).",
                            "children": [t for t in parsed if isinstance(t, dict)],
                        }
                    ]
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", po_output, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                        if isinstance(parsed, dict):
                            obj = parsed
                    except json.JSONDecodeError:
                        pass
                if not epics_raw and obj is None:
                    match_arr = re.search(r"\[.*\]", po_output, re.DOTALL)
                    if match_arr:
                        try:
                            parsed = json.loads(match_arr.group())
                            if isinstance(parsed, list):
                                epics_raw = [
                                    {
                                        "title": "Project backlog",
                                        "description": "Stories from plan (legacy flat array).",
                                        "children": [t for t in parsed if isinstance(t, dict)],
                                    }
                                ]
                        except json.JSONDecodeError:
                            pass

        if obj is not None and not epics_raw:
            raw_list = obj.get("epics")
            if isinstance(raw_list, list) and raw_list:
                epics_raw = [e for e in raw_list if isinstance(e, dict)]
            elif isinstance(obj.get("children"), list):
                epics_raw = [
                    {
                        "title": str(obj.get("title") or "Project backlog"),
                        "description": str(obj.get("description") or ""),
                        "children": [c for c in obj["children"] if isinstance(c, dict)],
                    }
                ]

    if not epics_raw:
        raise ValueError("No epics or task array found in PO plan output")

    for epic_raw in epics_raw:
        title = str(epic_raw.get("title") or "Untitled epic").strip()
        description = str(epic_raw.get("description") or title).strip()
        children_raw = epic_raw.get("children")
        if not isinstance(children_raw, list) or not children_raw:
            # Treat epic itself as a single child if children missing
            children_raw = [
                {
                    "title": title,
                    "description": description,
                    "acceptanceCriteria": epic_raw.get("acceptanceCriteria") or [description],
                }
            ]
        children = [_normalize_plan_child(c) for c in children_raw if isinstance(c, dict)]
        children = [c for c in children if c.get("title")]
        if not children:
            continue

        existing = _find_matching_feature(title, description)
        child_ids: List[str] = []
        if existing:
            feature_id = str(existing["id"])
            summary["reusedEpicIds"].append(feature_id)
            for child_payload in children:
                child = _spawn_child_task(feature_id, child_payload)
                cid = _link_child_to_feature(existing, child) if child else ""
                if cid:
                    child_ids.append(cid)
            append_feature_history(
                feature_id,
                {
                    "source": "plan",
                    "requestTitle": title,
                    "requestBody": description[:500],
                    "poSummary": f"Plan linked {len(child_ids)} child card(s) to existing epic",
                    "childTaskId": child_ids[0] if child_ids else "",
                },
            )
            record_feature_decision(
                feature_id,
                "Product Owner",
                "plan_epic",
                f"Plan reused epic '{title}'",
                f"Children: {', '.join(child_ids)}",
            )
            existing["featureRollup"] = build_feature_rollup(feature_id)
            summary["epicIds"].append(feature_id)
        else:
            feature, first_child = create_feature(
                title,
                description,
                request_title=title,
                request_body=description,
                child_task=children[0],
                po_summary=f"Created from plan with {len(children)} child card(s)",
                source="plan",
            )
            feature_id = str(feature["id"])
            if first_child and first_child.get("id"):
                child_ids.append(str(first_child["id"]))
            for child_payload in children[1:]:
                child = _spawn_child_task(feature_id, child_payload)
                cid = _link_child_to_feature(feature, child) if child else ""
                if cid:
                    child_ids.append(cid)
            feature["featureRollup"] = build_feature_rollup(feature_id)
            summary["epicIds"].append(feature_id)

        summary["childIds"].extend(child_ids)

    summary["epicCount"] = len(summary["epicIds"])
    summary["childCount"] = len(summary["childIds"])
    save_current_project_state()
    publish_board_update(source="plan_epics")
    return summary


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
