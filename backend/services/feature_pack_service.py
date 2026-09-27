"""Preview, approve, and export generated feature packs before board materialization."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def outline_fingerprint(outline_text: str) -> str:
    normalized = str(outline_text or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

from backend import state
from backend.services.feature_service import (
    _looks_like_dependency_only_child,
    _normalize_plan_child,
    apply_plan_epics_from_po_output,
    looks_like_usable_plan_epics,
)
from backend.services.plan_outline_quality import brief_epic_coverage_warnings
from backend.services.project_service import save_current_project_state
from backend.services.task_spec_validation import spec_readiness


def _pending_sidecar_path() -> str:
    ws = str(getattr(state, "WORKSPACE_DIR", "") or "").strip()
    if not ws:
        return ""
    return os.path.join(ws, "pending_feature_pack.json")


def save_pending_feature_pack_to_disk(pack: Dict[str, Any]) -> None:
    path = _pending_sidecar_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pack, f, indent=2)
    except OSError:
        pass


def load_pending_feature_pack_from_disk() -> Optional[Dict[str, Any]]:
    path = _pending_sidecar_path()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def clear_pending_feature_pack() -> None:
    state.PENDING_FEATURE_PACK = None
    path = _pending_sidecar_path()
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def set_pending_feature_pack(pack: Dict[str, Any]) -> None:
    state.PENDING_FEATURE_PACK = pack
    save_pending_feature_pack_to_disk(pack)
    save_current_project_state()


def get_pending_feature_pack() -> Optional[Dict[str, Any]]:
    if state.PENDING_FEATURE_PACK:
        return state.PENDING_FEATURE_PACK
    loaded = load_pending_feature_pack_from_disk()
    if loaded:
        state.PENDING_FEATURE_PACK = loaded
    return loaded


def _po_output_is_failed(po_output: str) -> bool:
    raw = str(po_output or "").strip()
    if not raw:
        return True
    lower = raw.lower()
    if raw.startswith("LLM_CALL_FAILED"):
        return True
    if raw.startswith("Max tool iterations") or "max tool iterations" in lower:
        return True
    return False


def _po_output_is_markdown_outline(po_output: str) -> bool:
    from backend.services.plan_outline_quality import looks_like_markdown_plan_attempt

    if not po_output or not str(po_output).strip():
        return False
    if looks_like_usable_plan_epics(po_output):
        return False
    return looks_like_markdown_plan_attempt(po_output)


def resolve_epics_json_string(po_output: str, outline_text: str) -> Tuple[str, str]:
    """Return (epics_json, source) where source is llm|fallback|empty."""
    from backend.services.feature_service import build_epics_json_from_plan_outline

    if po_output and looks_like_usable_plan_epics(po_output):
        return po_output, "llm"
    if po_output and _po_output_is_markdown_outline(po_output):
        return "", "markdown_reject"
    if _po_output_is_failed(po_output):
        fallback = build_epics_json_from_plan_outline(outline_text)
        return fallback, "fallback" if fallback else "empty"
    fallback = build_epics_json_from_plan_outline(outline_text)
    if fallback and not looks_like_usable_plan_epics(po_output or ""):
        return fallback, "fallback"
    if po_output and looks_like_usable_plan_epics(po_output):
        return po_output, "llm"
    return po_output or "", "llm_raw"


def _enrich_child(child: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_plan_child(child)
    probe = {
        "title": normalized.get("title"),
        "description": normalized.get("description"),
        "workType": normalized.get("workType"),
        "acceptanceCriteria": normalized.get("acceptanceCriteria"),
        "scope": child.get("scope"),
        "testPlan": child.get("testPlan"),
        "userStory": child.get("userStory"),
        "requiresDev": normalized.get("requiresDev"),
    }
    ready = spec_readiness(probe)
    return {**normalized, "specReadiness": ready}


def build_feature_pack_preview(
    epics_json: str,
    *,
    outline_text: str = "",
    source: str = "llm",
) -> Dict[str, Any]:
    from backend.services.po_clarification import extract_json_object_from_text

    issues: List[Dict[str, str]] = []
    obj = extract_json_object_from_text(epics_json) or {}
    epics_raw = obj.get("epics") if isinstance(obj, dict) else None
    if not isinstance(epics_raw, list):
        epics_raw = []

    preview_epics: List[Dict[str, Any]] = []
    child_ok = 0
    child_total = 0
    epic_titles: List[str] = []

    for epic in epics_raw:
        if not isinstance(epic, dict):
            continue
        title = str(epic.get("title") or "Untitled epic")
        epic_titles.append(title)
        children_raw = epic.get("children") if isinstance(epic.get("children"), list) else []
        children_out = []
        for c in children_raw:
            if not isinstance(c, dict):
                continue
            enriched = _enrich_child(c)
            child_total += 1
            if enriched.get("specReadiness", {}).get("ok"):
                child_ok += 1
            elif len(children_raw) == 1 and _looks_like_dependency_only_child(enriched):
                issues.append(
                    {
                        "severity": "fail",
                        "code": "dependency_only_child",
                        "message": f"Epic '{title}' has only a dependency-only child.",
                    }
                )
            children_out.append(enriched)
        preview_epics.append(
            {
                "title": title,
                "description": str(epic.get("description") or ""),
                "children": children_out,
            }
        )

    for w in brief_epic_coverage_warnings(state.PROJECT_BRIEF, epic_titles):
        issues.append({"severity": "warn", "code": "brief_coverage", "message": w})

    stats = {
        "epicCount": len(preview_epics),
        "childCount": child_total,
        "childrenSpecOk": child_ok,
        "source": source,
    }
    ok = child_total > 0 and child_ok == child_total and not any(
        i.get("severity") == "fail" for i in issues
    )
    return {
        "ok": ok,
        "epics": preview_epics,
        "issues": issues,
        "stats": stats,
        "epicsJson": epics_json,
        "outlineExcerpt": (outline_text or "")[:500],
        "outlineFingerprint": outline_fingerprint(outline_text),
        "status": "preview",
    }


def preview_plan_backlog_from_outputs(po_output: str, outline_text: str) -> Dict[str, Any]:
    epics_json, source = resolve_epics_json_string(po_output, outline_text)
    if not epics_json or source == "markdown_reject":
        return {
            "ok": False,
            "epics": [],
            "issues": [
                {
                    "severity": "fail",
                    "code": "no_epics",
                    "message": "Could not parse epics JSON from PO output.",
                }
            ],
            "stats": {"epicCount": 0, "childCount": 0, "childrenSpecOk": 0, "source": source},
            "epicsJson": "",
            "status": "preview",
        }
    return build_feature_pack_preview(epics_json, outline_text=outline_text, source=source)


def approve_pending_feature_pack() -> Dict[str, Any]:
    pack = get_pending_feature_pack()
    if not pack or not pack.get("epicsJson"):
        return {"ok": False, "error": "No pending feature pack to approve."}
    epics_json = str(pack["epicsJson"])
    result = apply_plan_epics_from_po_output(epics_json)
    pack["status"] = "approved"
    clear_pending_feature_pack()
    return {"ok": True, "result": result}


def build_features_export_markdown(pack: Optional[Dict[str, Any]] = None) -> str:
    pack = pack or get_pending_feature_pack() or {}
    lines = [
        f"# Project: {state.PROJECT_NAME}",
        "",
        "## Plan outline",
        "",
        (state.PROJECT_PLAN_OUTLINE or "(none)").strip(),
        "",
        f"## Generated features ({pack.get('status', 'preview')})",
        "",
    ]
    for epic in pack.get("epics") or []:
        lines.append(f"### Epic: {epic.get('title', '?')}")
        lines.append(str(epic.get("description") or ""))
        lines.append("")
        for child in epic.get("children") or []:
            lines.append(f"- **Child:** {child.get('title', '?')}")
            lines.append(f"  - description: {child.get('description', '')}")
            if child.get("scope"):
                lines.append(f"  - scope: {child.get('scope')}")
            if child.get("testPlan"):
                lines.append(f"  - testPlan: {child.get('testPlan')}")
            ac = child.get("acceptanceCriteria") or []
            if ac:
                lines.append("  - AC:")
                for c in ac:
                    lines.append(f"    - {c}")
            ready = child.get("specReadiness") or {}
            if ready.get("ok"):
                lines.append("  - Spec readiness: OK")
            else:
                missing = ", ".join(ready.get("missing") or [])
                warns = ", ".join(ready.get("warnings") or [])
                lines.append(f"  - Spec readiness: gaps — {missing}; warnings — {warns}")
            lines.append("")
    return "\n".join(lines)


def build_features_export_json(pack: Optional[Dict[str, Any]] = None) -> str:
    pack = pack or get_pending_feature_pack() or {}
    payload = {
        "projectName": state.PROJECT_NAME,
        "projectId": state.CURRENT_PROJECT_ID,
        "planOutline": state.PROJECT_PLAN_OUTLINE,
        "featurePack": pack,
    }
    return json.dumps(payload, indent=2)
