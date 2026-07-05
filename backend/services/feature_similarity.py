"""Deterministic feature similarity scoring and task linking."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend import state
from backend.agents.task_context import find_task_by_id, is_task_done, normalize_task
from backend.services.board_service import publish_board_update
from backend.services.logs import add_system_log

RELATED_THRESHOLD = 0.35
BLOCKED_THRESHOLD = 0.65

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "is",
        "are",
        "be",
        "as",
        "at",
        "by",
        "from",
        "this",
        "that",
        "it",
        "user",
        "feature",
        "implement",
        "add",
        "create",
        "update",
        "support",
    }
)

DOMAIN_KEYWORDS = frozenset(
    {
        "auth",
        "login",
        "logout",
        "api",
        "bloc",
        "user",
        "admin",
        "database",
        "db",
        "ui",
        "screen",
        "widget",
        "test",
        "qa",
        "payment",
        "order",
        "cart",
        "profile",
        "settings",
        "notification",
        "search",
        "cache",
        "sync",
        "security",
        "token",
        "session",
    }
)


def _tokenize(text: str) -> Set[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in tokens if len(t) > 1 and t not in STOPWORDS}


def _task_text(task: Dict[str, Any]) -> str:
    normalize_task(task)
    parts = [str(task.get("title", "")), str(task.get("description", ""))]
    return " ".join(parts)


def _file_path_tokens(task: Dict[str, Any]) -> Set[str]:
    paths: Set[str] = set()
    for f in task.get("files") or []:
        if isinstance(f, str):
            paths.add(f)
        elif isinstance(f, dict) and f.get("path"):
            paths.add(str(f["path"]))
    tokens: Set[str] = set()
    for path in paths:
        for part in re.split(r"[/\\._-]+", path.lower()):
            if len(part) > 2:
                tokens.add(part)
    return tokens


def score_task_similarity(
    new_task: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Tuple[float, List[str]]:
    """Return similarity score in [0, 1] and human-readable reasons."""
    new_tokens = _tokenize(_task_text(new_task))
    cand_tokens = _tokenize(_task_text(candidate))
    if not new_tokens or not cand_tokens:
        return 0.0, []

    intersection = new_tokens & cand_tokens
    union = new_tokens | cand_tokens
    jaccard = len(intersection) / len(union) if union else 0.0

    domain_new = new_tokens & DOMAIN_KEYWORDS
    domain_cand = cand_tokens & DOMAIN_KEYWORDS
    domain_overlap = domain_new & domain_cand
    domain_bonus = 0.15 * min(len(domain_overlap), 3) / 3 if domain_overlap else 0.0

    path_new = _file_path_tokens(new_task)
    path_cand = _file_path_tokens(candidate)
    path_overlap = path_new & path_cand
    path_bonus = 0.1 if path_overlap else 0.0

    title_new = str(new_task.get("title", "")).lower()
    title_cand = str(candidate.get("title", "")).lower()
    title_bonus = 0.0
    if title_new and title_cand and (title_new in title_cand or title_cand in title_new):
        title_bonus = 0.2

    score = min(1.0, jaccard + domain_bonus + path_bonus + title_bonus)
    reasons: List[str] = []
    if intersection:
        reasons.append(f"shared terms: {', '.join(sorted(intersection)[:5])}")
    if domain_overlap:
        reasons.append(f"domain: {', '.join(sorted(domain_overlap)[:3])}")
    if path_overlap:
        reasons.append(f"paths: {', '.join(sorted(path_overlap)[:3])}")
    if title_bonus:
        reasons.append("similar titles")
    return score, reasons


def iter_board_tasks(exclude_ids: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    exclude = exclude_ids or set()
    tasks: List[Dict[str, Any]] = []
    for lane_tasks in state.SHARED_BOARD.values():
        for task in lane_tasks:
            tid = str(task.get("id", ""))
            if tid and tid not in exclude:
                tasks.append(task)
    return tasks


def link_related_features(
    task: Dict[str, Any],
    *,
    exclude_ids: Optional[Set[str]] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Link task to similar existing features. Returns ids that were linked."""
    normalize_task(task)
    task_id = str(task.get("id", ""))
    exclude = set(exclude_ids or [])
    exclude.add(task_id)
    pool = candidates if candidates is not None else iter_board_tasks(exclude_ids=exclude)

    linked: List[str] = []
    related_ids: List[str] = list(task.get("relatedTaskIds") or [])
    blocked: List[str] = list(task.get("blockedBy") or [])

    scored: List[Tuple[float, Dict[str, Any], List[str]]] = []
    for candidate in pool:
        cand_id = str(candidate.get("id", ""))
        if not cand_id or cand_id in exclude:
            continue
        score, reasons = score_task_similarity(task, candidate)
        if score >= RELATED_THRESHOLD:
            scored.append((score, candidate, reasons))

    scored.sort(key=lambda x: x[0], reverse=True)

    for score, candidate, reasons in scored[:5]:
        cand_id = str(candidate["id"])
        if cand_id not in related_ids:
            related_ids.append(cand_id)
            linked.append(cand_id)

        if score >= BLOCKED_THRESHOLD and not is_task_done(cand_id):
            if cand_id not in blocked:
                blocked.append(cand_id)

        other = find_task_by_id(cand_id)
        if other:
            normalize_task(other)
            reverse_related = list(other.get("relatedTaskIds") or [])
            if task_id and task_id not in reverse_related:
                reverse_related.append(task_id)
                other["relatedTaskIds"] = reverse_related

        reason_text = "; ".join(reasons) if reasons else f"score {score:.2f}"
        add_system_log(
            "Product Owner",
            "info",
            f"Linked '{task.get('title', task_id)}' ↔ related: {cand_id} ({reason_text})",
        )

    task["relatedTaskIds"] = related_ids
    task["blockedBy"] = blocked
    if linked:
        publish_board_update(task_id, source="related_features")
    return linked
