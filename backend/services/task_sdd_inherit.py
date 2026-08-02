"""Auto-fill SDD fields from feature epic context (empty fields only)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.agents.task_context import coerce_task_text, normalize_acceptance_criteria


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    match = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    return match[0].strip() if match else text[:200].strip()


def _card_slice_scope(task: Dict[str, Any]) -> str:
    lines: List[str] = []
    title = coerce_task_text(task.get("title")).strip()
    desc = coerce_task_text(task.get("description")).strip()
    if title:
        lines.append(f"- {title}")
    if desc:
        for ln in desc.splitlines():
            s = ln.strip()
            if s:
                lines.append(f"- {s}" if not s.startswith("-") else s)
    for ac in normalize_acceptance_criteria(task.get("acceptanceCriteria")):
        lines.append(f"- AC: {ac}")
    return "\n".join(lines[:20])


def _feature_user_story(feature: Dict[str, Any]) -> str:
    ft = coerce_task_text(feature.get("title")).strip()
    goal = _first_sentence(coerce_task_text(feature.get("description")))
    if ft and goal:
        return f"As a user, I want {ft}, so that {goal.rstrip('.')}."
    if ft:
        return f"As a user, I want {ft}."
    return ""


def apply_feature_sdd_defaults(
    task: Dict[str, Any],
    feature: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Fill empty SDD fields when task has featureId. Returns list of field names inherited."""
    inherited: List[str] = []
    fid = str(task.get("featureId") or "").strip()
    if not fid:
        return inherited

    if feature is None:
        from backend.services.feature_service import find_feature_by_id

        feature = find_feature_by_id(fid)
    if not feature:
        return inherited

    meta = list(task.get("sddInheritedFromFeature") or [])
    if not isinstance(meta, list):
        meta = []

    if not coerce_task_text(task.get("userStory")).strip():
        story = _feature_user_story(feature)
        if story:
            task["userStory"] = story
            inherited.append("userStory")
            if "userStory" not in meta:
                meta.append("userStory")

    if not coerce_task_text(task.get("scope")).strip():
        scope = _card_slice_scope(task)
        if scope:
            task["scope"] = scope
            inherited.append("scope")
            if "scope" not in meta:
                meta.append("scope")

    if inherited:
        task["sddInheritedFromFeature"] = meta
    return inherited
