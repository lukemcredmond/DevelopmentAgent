"""Brief-derived categories and role-aware skill suggestions."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from backend import state
from backend.services.skills import get_skill_metadata, scan_skills_directory

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "csharp": ["c#", "csharp", ".net", "asp.net", "blazor", "dotnet"],
    "web": ["web", "react", "vue", "angular", "frontend", "html", "css"],
    "vr": ["vr", "quest", "unity", "xr", "oculus", "meta quest"],
    "flutter": ["flutter", "dart"],
    "python": ["python", "django", "fastapi", "pytest"],
    "javascript": ["javascript", "typescript", "node", "npm"],
}

CATEGORY_LABELS: Dict[str, str] = {
    "csharp": "C# / .NET",
    "web": "Web development",
    "vr": "VR / Unity",
    "flutter": "Flutter",
    "python": "Python",
    "javascript": "JavaScript / TypeScript",
    "product": "Product",
}


def _workspace_category_hints() -> Set[str]:
    """Infer stack categories from workspace files on disk."""
    hints: Set[str] = set()
    ws = state.WORKSPACE_DIR
    if not ws or not os.path.isdir(ws):
        return hints

    try:
        from backend.workspace.files import _workspace_has_dotnet_project

        if _workspace_has_dotnet_project(ws):
            hints.add("csharp")
    except Exception:
        pass

    if os.path.isfile(os.path.join(ws, "pubspec.yaml")):
        hints.add("flutter")
    if os.path.isfile(os.path.join(ws, "package.json")):
        hints.add("javascript")
        hints.add("web")

    try:
        names = os.listdir(ws)
    except OSError:
        names = []
    if any(n.endswith(".py") for n in names):
        hints.add("python")

    return hints


def extract_brief_categories(brief: str) -> List[str]:
    """Return sorted category ids detected from brief text (+ workspace hints)."""
    text = (brief or "").lower()
    found: Set[str] = set()

    if text.strip():
        found.add("product")
        for cat_id, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                found.add(cat_id)

    found.update(_workspace_category_hints())
    return sorted(found)


def brief_categories_for_ui(brief: str) -> List[Dict[str, str]]:
    """Category ids with human labels for API/UI."""
    return [
        {"id": cat_id, "label": CATEGORY_LABELS.get(cat_id, cat_id.title())}
        for cat_id in extract_brief_categories(brief)
    ]


def _category_overlap_score(brief_cats: Set[str], skill_cats: List[str]) -> tuple[int, Optional[str]]:
    overlap = brief_cats.intersection(skill_cats)
    if not overlap:
        return 0, None
    best = sorted(overlap)[0]
    label = CATEGORY_LABELS.get(best, best.title())
    return len(overlap) * 2, f"Matches {label}"


def _agent_allowed_for_skill(agent: str, filename: str, meta: Dict[str, Any]) -> bool:
    if meta.get("po_only") and agent != "po":
        return False
    if meta.get("cr_only") and agent != "cr":
        return False
    return True


def score_skills_for_agent(
    agent: str,
    *,
    brief: Optional[str] = None,
    assigned: Optional[List[str]] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Score and rank skills for an agent based on brief categories."""
    brief_text = brief if brief is not None else (state.PROJECT_BRIEF or "")
    brief_cats = set(extract_brief_categories(brief_text))
    assigned_set = set(assigned or [])

    from backend.agents.registry import AGENT_MAP

    if assigned is None and agent in AGENT_MAP:
        assigned_set = set(AGENT_MAP[agent].assigned_skills)

    scored: List[Dict[str, Any]] = []

    for skill in scan_skills_directory():
        filename = skill["filename"]
        if filename in assigned_set:
            continue

        meta = get_skill_metadata(filename)
        if not _agent_allowed_for_skill(agent, filename, meta):
            continue

        score = 0
        reason_parts: List[str] = []

        if agent in meta.get("agents", []):
            score += 1

        overlap_score, overlap_reason = _category_overlap_score(brief_cats, meta.get("categories", []))
        score += overlap_score
        if overlap_reason:
            reason_parts.append(overlap_reason)

        if meta.get("all_stacks") and agent == "cr" and brief_cats - {"product"}:
            stack_cats = brief_cats - {"product"}
            score += len(stack_cats)
            if stack_cats:
                labels = [CATEGORY_LABELS.get(c, c) for c in sorted(stack_cats)[:2]]
                reason_parts.append(f"Review stack: {', '.join(labels)}")

        if meta.get("universal") and agent in meta.get("agents", []):
            score += 1
            if not reason_parts:
                reason_parts.append("General workflow")

        if score <= 0:
            continue

        reason = reason_parts[0] if reason_parts else "Suggested for role"
        scored.append(
            {
                "filename": filename,
                "title": skill.get("title") or filename,
                "score": score,
                "reason": reason,
            }
        )

    scored.sort(key=lambda s: (-s["score"], s["filename"].lower()))
    return scored[: max(1, min(limit, 20))]


def build_suggestions_response(agent: str, limit: int = 5) -> Dict[str, Any]:
    """Full API payload for skill suggestions."""
    brief = state.PROJECT_BRIEF or ""
    return {
        "briefCategories": brief_categories_for_ui(brief),
        "suggestions": score_skills_for_agent(agent, brief=brief, limit=limit),
    }
