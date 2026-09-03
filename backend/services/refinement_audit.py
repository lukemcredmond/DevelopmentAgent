"""Audit Refinement lane for duplicate cards and quality issues."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.agents.task_context import find_task_by_id, get_task_lane, normalize_task, record_task_decision
from backend.services.board_service import move_board_stage, publish_board_update
from backend.services.feature_similarity import REUSE_THRESHOLD, score_task_similarity

DUPLICATE_CLUSTER_THRESHOLD = 0.72
EXACT_TITLE_MIN_LEN = 4


def _normalize_title(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return text


def _pick_canonical_task(members: List[Dict[str, Any]]) -> str:
    """Prefer the richest card to keep when consolidating duplicates."""

    def richness(task: Dict[str, Any]) -> Tuple[int, int, int, int]:
        normalize_task(task)
        ac = len([c for c in (task.get("acceptanceCriteria") or []) if str(c).strip()])
        desc = len(str(task.get("description") or ""))
        notes = len(str(task.get("refinementNotes") or ""))
        complete = 1 if task.get("refinementComplete") else 0
        return (complete, ac, desc + notes, ac)

    best = max(members, key=richness)
    return str(best.get("id") or "")


class _UnionFind:
    def __init__(self, ids: List[str]) -> None:
        self.parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _audit_task_quality(task: Dict[str, Any]) -> List[str]:
    normalize_task(task)
    reasons: List[str] = []
    title = str(task.get("title") or "").strip()
    if len(title) < 3:
        reasons.append("Missing or very short title")
    desc = str(task.get("description") or "").strip()
    ac = [c for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if not desc and not ac:
        reasons.append("No description and no acceptance criteria")
    if str(task.get("workType") or "") == "feature":
        reasons.append("Feature epic — usually belongs in Features, not Refinement")
    if task.get("refinementComplete") is True:
        reasons.append("refinementComplete is true but card is still in Refinement")
    parent_id = str(task.get("parentId") or "").strip()
    if parent_id and not find_task_by_id(parent_id):
        reasons.append(f"parentId {parent_id} not found on board")
    subtasks = task.get("subtasks") or []
    if isinstance(subtasks, list) and len(subtasks) > 0 and not ac and len(title) < 20:
        reasons.append("Has subtasks but thin parent card")
    return reasons


def _build_duplicate_clusters(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(tasks) < 2:
        return []

    by_id = {str(t.get("id") or ""): t for t in tasks if str(t.get("id") or "")}
    ids = list(by_id.keys())
    uf = _UnionFind(ids)
    pair_scores: Dict[Tuple[str, str], Tuple[float, List[str]]] = {}
    match_kinds: Dict[frozenset, str] = {}

    # Exact normalized titles
    title_buckets: Dict[str, List[str]] = {}
    for tid, task in by_id.items():
        norm = _normalize_title(str(task.get("title") or ""))
        if len(norm) >= EXACT_TITLE_MIN_LEN:
            title_buckets.setdefault(norm, []).append(tid)
    for bucket in title_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(1, len(bucket)):
            uf.union(bucket[0], bucket[i])
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                pair_scores[(bucket[i], bucket[j])] = (1.0, ["exact normalized title"])
                pair_scores[(bucket[j], bucket[i])] = (1.0, ["exact normalized title"])
        key = frozenset(bucket)
        match_kinds[key] = "exact_title"

    # Similarity pairs
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            score, reasons = score_task_similarity(by_id[a], by_id[b])
            if score >= DUPLICATE_CLUSTER_THRESHOLD:
                uf.union(a, b)
                pair_scores[(a, b)] = (score, reasons)
                pair_scores[(b, a)] = (score, reasons)

    groups: Dict[str, List[str]] = {}
    for tid in ids:
        root = uf.find(tid)
        groups.setdefault(root, []).append(tid)

    clusters: List[Dict[str, Any]] = []
    cluster_idx = 0
    for member_ids in groups.values():
        if len(member_ids) < 2:
            continue
        cluster_idx += 1
        members_raw = [by_id[mid] for mid in member_ids]
        keep_id = _pick_canonical_task(members_raw)
        member_set = frozenset(member_ids)
        kind = match_kinds.get(member_set)
        if not kind:
            kind = "similar"
            for mid in member_ids:
                if mid != keep_id:
                    sc, _ = pair_scores.get((mid, keep_id), (0.0, []))
                    if sc >= REUSE_THRESHOLD:
                        kind = "likely_same_request"
                        break

        max_score = 0.0
        members_out: List[Dict[str, Any]] = []
        for mid in sorted(member_ids):
            task = by_id[mid]
            sc, reasons = pair_scores.get((mid, keep_id), (0.0, []))
            if mid == keep_id:
                sc = 1.0
            max_score = max(max_score, sc)
            members_out.append(
                {
                    "taskId": mid,
                    "title": str(task.get("title") or ""),
                    "similarityToKeep": round(sc, 3),
                    "reasons": reasons if mid != keep_id else ["suggested keep"],
                    "isSuggestedKeep": mid == keep_id,
                }
            )

        clusters.append(
            {
                "clusterId": f"dup-{cluster_idx}",
                "matchKind": kind,
                "maxScore": round(max_score, 3),
                "suggestedKeepTaskId": keep_id,
                "memberCount": len(member_ids),
                "members": members_out,
                "removableTaskIds": [mid for mid in member_ids if mid != keep_id],
            }
        )

    clusters.sort(key=lambda c: (-int(c["memberCount"]), -float(c["maxScore"]), c["clusterId"]))
    return clusters


def audit_refinement_lane(board: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    from backend import state

    board = board if board is not None else state.SHARED_BOARD
    tasks = [t for t in (board.get("Refinement") or []) if isinstance(t, dict)]
    for t in tasks:
        normalize_task(t)

    clusters = _build_duplicate_clusters(tasks)
    removable_ids: Set[str] = set()
    for cluster in clusters:
        for tid in cluster.get("removableTaskIds") or []:
            removable_ids.add(str(tid))

    quality_issues: List[Dict[str, Any]] = []
    for task in tasks:
        reasons = _audit_task_quality(task)
        if not reasons:
            continue
        quality_issues.append(
            {
                "taskId": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "reasons": reasons,
            }
        )

    return {
        "totalRefinement": len(tasks),
        "duplicateClusterCount": len(clusters),
        "duplicateExtraCount": len(removable_ids),
        "qualityIssueCount": len(quality_issues),
        "estimatedUniqueAfterMerge": len(tasks) - len(removable_ids),
        "clusters": clusters,
        "qualityIssues": quality_issues,
        "defaultRemoveTaskIds": sorted(removable_ids),
    }


def apply_refinement_audit_actions(
    *,
    delete_task_ids: Optional[List[str]] = None,
    move_to_done_task_ids: Optional[List[str]] = None,
    move_to_backlog_task_ids: Optional[List[str]] = None,
    duplicate_of_by_task_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    from backend import state
    from backend.services.project_service import save_current_project_state

    delete_task_ids = [str(x).strip() for x in (delete_task_ids or []) if str(x).strip()]
    move_to_done_task_ids = [str(x).strip() for x in (move_to_done_task_ids or []) if str(x).strip()]
    move_to_backlog_task_ids = [
        str(x).strip() for x in (move_to_backlog_task_ids or []) if str(x).strip()
    ]
    dup_map = {str(k): str(v) for k, v in (duplicate_of_by_task_id or {}).items() if k and v}

    deleted: List[str] = []
    moved_done: List[str] = []
    moved_backlog: List[str] = []
    skipped: List[str] = []

    def _in_refinement(tid: str) -> bool:
        return get_task_lane(tid) == "Refinement"

    for tid in delete_task_ids:
        if not _in_refinement(tid):
            skipped.append(tid)
            continue
        removed = False
        for lane, lane_tasks in state.SHARED_BOARD.items():
            for task in list(lane_tasks):
                if str(task.get("id", "")) == tid:
                    lane_tasks.remove(task)
                    removed = True
                    break
            if removed:
                break
        if not removed:
            skipped.append(tid)
            continue
        record_task_decision(
            tid,
            "User",
            "refinement_audit",
            "Refinement cleanup: deleted as duplicate or invalid",
        )
        publish_board_update(tid, source="refinement_audit")
        deleted.append(tid)

    for tid in move_to_done_task_ids:
        if tid in deleted:
            continue
        if not _in_refinement(tid):
            skipped.append(tid)
            continue
        keep = dup_map.get(tid, "")
        note = f"Refinement cleanup: consolidated duplicate"
        if keep:
            note += f" — see {keep}"
        result = move_board_stage(tid, "Done")
        if result.startswith("Error"):
            skipped.append(tid)
            continue
        task = find_task_by_id(tid)
        if task:
            normalize_task(task)
            task["actualSummary"] = note[:400]
        record_task_decision(tid, "User", "refinement_audit", note)
        moved_done.append(tid)

    for tid in move_to_backlog_task_ids:
        if tid in deleted or tid in moved_done:
            continue
        if not _in_refinement(tid):
            skipped.append(tid)
            continue
        result = move_board_stage(tid, "Backlog")
        if result.startswith("Error"):
            skipped.append(tid)
            continue
        record_task_decision(
            tid,
            "User",
            "refinement_audit",
            "Refinement cleanup: moved to Backlog for re-triage",
        )
        moved_backlog.append(tid)

    if deleted or moved_done or moved_backlog:
        save_current_project_state(force_board=True)

    return {
        "ok": True,
        "deleted": deleted,
        "movedDone": moved_done,
        "movedBacklog": moved_backlog,
        "skipped": skipped,
    }
