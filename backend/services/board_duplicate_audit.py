"""Board-wide duplicate / overlapping card audit (Needs PO, In Progress, Backlog, Features)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.agents.task_context import find_task_by_id, get_task_lane, normalize_task, record_task_decision
from backend.services.board_service import move_board_stage, publish_board_update
from backend.services.feature_similarity import REUSE_THRESHOLD, score_task_similarity
from backend.services.logs import add_system_log
from backend.services.refinement_audit import (
    DUPLICATE_CLUSTER_THRESHOLD,
    EXACT_TITLE_MIN_LEN,
    _UnionFind,
    _normalize_title,
    _pick_canonical_task,
)
from backend.services.sprint_speed_gates import latch_needs_po_auto_skip

AUDIT_LANES = ("Needs PO", "In Progress", "Backlog", "Features")
EXPORT_DOMAIN_TOKENS = frozenset(
    {"export", "import", "share_intent", "json_export", "json_import", "share"}
)


def _task_primary_files(task: Dict[str, Any]) -> Set[str]:
    paths: Set[str] = set()
    for f in task.get("files") or []:
        if isinstance(f, str) and f.strip():
            paths.add(f.strip().replace("\\", "/"))
        elif isinstance(f, dict) and f.get("path"):
            paths.add(str(f["path"]).strip().replace("\\", "/"))
    lint_src = str(task.get("lintSourceFile") or "").strip()
    if lint_src:
        paths.add(lint_src.replace("\\", "/"))
    return paths


def _edit_target(task: Dict[str, Any]) -> str:
    try:
        from backend.services.file_blocker import resolve_dev_edit_target_path

        return str(resolve_dev_edit_target_path(task) or "").strip().replace("\\", "/")
    except Exception:
        return ""


def _blocked_by_set(task: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for raw in task.get("blockedBy") or []:
        s = str(raw or "").strip()
        if s:
            out.add(s)
    return out


def _blocked_by_jaccard(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    sa, sb = _blocked_by_set(a), _blocked_by_set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def achievement_summary(task: Dict[str, Any]) -> str:
    normalize_task(task)
    title = str(task.get("title") or "").strip()
    for ac in task.get("acceptanceCriteria") or []:
        text = str(ac or "").strip()
        if text:
            return f"{title} — {text[:160]}"
    desc = str(task.get("description") or "").strip()
    if desc:
        first = desc.split(".")[0].strip() or desc[:160]
        return f"{title} — {first[:160]}"
    return title or str(task.get("id") or "")


def _recommended_action(members: List[Dict[str, Any]]) -> str:
    for task in members:
        ac = [c for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
        if not ac and len(str(task.get("description") or "").strip()) < 20:
            return "move_extras_done"
    return "block_extras"


def _collect_audit_tasks(
    board: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    from backend import state

    board = board if board is not None else state.SHARED_BOARD
    tasks: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for lane in AUDIT_LANES:
        for task in board.get(lane) or []:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            normalize_task(task)
            tasks.append(task)
    return tasks


def _build_board_duplicate_clusters(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(tasks) < 2:
        return []

    by_id = {str(t.get("id") or ""): t for t in tasks if str(t.get("id") or "")}
    ids = list(by_id.keys())
    uf = _UnionFind(ids)
    pair_scores: Dict[Tuple[str, str], Tuple[float, List[str]]] = {}
    pair_kind: Dict[Tuple[str, str], str] = {}

    def link(a: str, b: str, score: float, reasons: List[str], kind: str) -> None:
        uf.union(a, b)
        pair_scores[(a, b)] = (score, reasons)
        pair_scores[(b, a)] = (score, reasons)
        pair_kind[(a, b)] = kind
        pair_kind[(b, a)] = kind

    title_buckets: Dict[str, List[str]] = {}
    for tid, task in by_id.items():
        norm = _normalize_title(str(task.get("title") or ""))
        if len(norm) >= EXACT_TITLE_MIN_LEN:
            title_buckets.setdefault(norm, []).append(tid)
    for bucket in title_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(1, len(bucket)):
            link(bucket[0], bucket[i], 1.0, ["exact normalized title"], "exact_title")

    edit_buckets: Dict[str, List[str]] = {}
    file_buckets: Dict[str, List[str]] = {}
    for tid, task in by_id.items():
        target = _edit_target(task)
        if target:
            edit_buckets.setdefault(target, []).append(tid)
        for path in _task_primary_files(task):
            file_buckets.setdefault(path, []).append(tid)

    for bucket in edit_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(1, len(bucket)):
            link(bucket[0], bucket[i], 0.95, [f"same edit target"], "same_edit_target")

    for bucket in file_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(1, len(bucket)):
            link(
                bucket[0],
                bucket[i],
                0.9,
                ["shared primary file path"],
                "same_primary_file",
            )

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            jac = _blocked_by_jaccard(by_id[a], by_id[b])
            if jac >= 0.5:
                link(a, b, max(0.75, jac), ["blockedBy overlap"], "dependency_overlap")
                continue
            score, reasons = score_task_similarity(by_id[a], by_id[b])
            blob = f"{by_id[a].get('title')} {by_id[b].get('title')}".lower()
            if any(tok in blob for tok in EXPORT_DOMAIN_TOKENS):
                score = min(1.0, score + 0.08)
                reasons = list(reasons) + ["export/import domain"]
            if score >= DUPLICATE_CLUSTER_THRESHOLD:
                link(a, b, score, reasons, "similar")

    groups: Dict[str, List[str]] = {}
    for tid in ids:
        groups.setdefault(uf.find(tid), []).append(tid)

    clusters: List[Dict[str, Any]] = []
    cluster_idx = 0
    for member_ids in groups.values():
        if len(member_ids) < 2:
            continue
        cluster_idx += 1
        members_raw = [by_id[mid] for mid in member_ids]
        keep_id = _pick_canonical_task(members_raw)
        keep_task = by_id[keep_id]

        max_score = 0.0
        kind = "similar"
        members_out: List[Dict[str, Any]] = []
        for mid in sorted(member_ids):
            task = by_id[mid]
            sc, reasons = pair_scores.get((mid, keep_id), (0.0, []))
            if mid == keep_id:
                sc = 1.0
            max_score = max(max_score, sc)
            pk = pair_kind.get((mid, keep_id), "similar")
            if mid != keep_id and sc >= max_score * 0.99:
                kind = pk
            lane = get_task_lane(mid) or ""
            members_out.append(
                {
                    "taskId": mid,
                    "title": str(task.get("title") or ""),
                    "lane": lane,
                    "similarityToKeep": round(sc, 3),
                    "reasons": reasons if mid != keep_id else ["suggested keep"],
                    "isSuggestedKeep": mid == keep_id,
                }
            )

        clusters.append(
            {
                "clusterId": f"board-dup-{cluster_idx}",
                "matchKind": kind,
                "confidence": round(max_score, 3),
                "achievementSummary": achievement_summary(keep_task),
                "recommendedAction": _recommended_action(members_raw),
                "suggestedKeepTaskId": keep_id,
                "memberCount": len(member_ids),
                "members": members_out,
                "removableTaskIds": [mid for mid in member_ids if mid != keep_id],
            }
        )

    clusters.sort(
        key=lambda c: (-int(c["memberCount"]), -float(c["confidence"]), c["clusterId"])
    )
    return clusters


def audit_board_duplicates(
    board: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    tasks = _collect_audit_tasks(board)
    clusters = _build_board_duplicate_clusters(tasks)
    removable: Set[str] = set()
    for cluster in clusters:
        for tid in cluster.get("removableTaskIds") or []:
            removable.add(str(tid))
    return {
        "lanesScanned": list(AUDIT_LANES),
        "tasksScanned": len(tasks),
        "duplicateClusterCount": len(clusters),
        "duplicateExtraCount": len(removable),
        "estimatedUniqueAfterMerge": len(tasks) - len(removable),
        "clusters": clusters,
        "defaultRemoveTaskIds": sorted(removable),
    }


def duplicate_audit_summary() -> Dict[str, Any]:
    """Lightweight counts for sprint rollup attachment."""
    report = audit_board_duplicates()
    return {
        "duplicateClusterCount": report.get("duplicateClusterCount"),
        "duplicateExtraCount": report.get("duplicateExtraCount"),
    }


def apply_board_duplicate_audit(
    *,
    apply_recommended: bool = False,
    duplicate_of_by_task_id: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    from backend import state
    from backend.services.project_service import save_current_project_state

    report = audit_board_duplicates()
    dup_map: Dict[str, str] = dict(duplicate_of_by_task_id or {})

    if apply_recommended:
        for cluster in report.get("clusters") or []:
            keep = str(cluster.get("suggestedKeepTaskId") or "")
            for tid in cluster.get("removableTaskIds") or []:
                dup_map[str(tid)] = keep

    blocked: List[str] = []
    skipped: List[str] = []

    cluster_by_tid: Dict[str, Dict[str, Any]] = {}
    for cluster in report.get("clusters") or []:
        for tid in cluster.get("removableTaskIds") or []:
            cluster_by_tid[str(tid)] = cluster

    for tid, keep_id in dup_map.items():
        if not tid or tid == keep_id:
            continue
        task = find_task_by_id(tid)
        if not task:
            skipped.append(tid)
            continue
        lane = get_task_lane(tid) or ""
        if lane not in AUDIT_LANES and lane != "Blocked":
            skipped.append(tid)
            continue

        cluster = cluster_by_tid.get(tid) or {}
        cluster_id = str(cluster.get("clusterId") or "")
        summary = str(cluster.get("achievementSummary") or achievement_summary(task))
        action = str(cluster.get("recommendedAction") or "block_extras")

        if action == "move_extras_done":
            result = move_board_stage(tid, "Done")
        else:
            result = move_board_stage(tid, "Blocked")

        if result.startswith("Error"):
            skipped.append(tid)
            continue

        live = find_task_by_id(tid)
        if live:
            live["duplicateOfTaskId"] = keep_id
            if cluster_id:
                live["duplicateClusterId"] = cluster_id
            note = f"Duplicate of {keep_id}: {summary[:200]}"
            latch_needs_po_auto_skip(live, reason=note[:300])
            record_task_decision(tid, "User", "duplicate_audit", note[:400])

        publish_board_update(tid, source="duplicate_audit")
        blocked.append(tid)

    if blocked:
        save_current_project_state(force_board=True)
        try:
            from backend.services.file_blocker import reconcile_file_blockers_from_board

            reconcile_file_blockers_from_board()
        except Exception:
            pass
        add_system_log(
            "System",
            "info",
            f"Duplicate audit: consolidated {len(blocked)} card(s) (keep canonical, extras blocked/Done)",
        )

    return {
        "ok": True,
        "blockedOrDone": blocked,
        "skipped": skipped,
        "appliedPairs": len(dup_map),
    }
