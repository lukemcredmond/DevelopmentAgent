"""Infer writePaths on PO-created or clarified tasks so Dev has an edit target."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_PATH_IN_TEXT = re.compile(
    r"(?:^|[\s`'\"(])(lib/[\w./-]+\.(?:dart|yaml)|test/[\w./-]+\.dart|pubspec\.yaml|analysis_options\.yaml)",
    re.IGNORECASE,
)


_WRITE_FILE_ACTIONS = frozenset(
    {"write", "written", "edited", "patch", "patched", "created", ""}
)


def infer_write_paths_for_po_task(
    task: Dict[str, Any],
    *,
    parent: Optional[Dict[str, Any]] = None,
) -> List[str]:
    ordered: List[str] = []
    seen: set = set()

    def _add(raw: Any) -> None:
        p = str(raw or "").strip().replace("\\", "/")
        if not p or p in seen:
            return
        seen.add(p)
        ordered.append(p)

    for key in ("writePaths", "write_paths"):
        for item in task.get(key) or []:
            _add(item)
    for key in ("writePath", "write_path", "file", "path", "targetFile", "target_file"):
        _add(task.get(key))
    blob = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("description") or ""),
            " ".join(str(c) for c in (task.get("acceptanceCriteria") or [])),
        ]
    )
    for match in _PATH_IN_TEXT.finditer(blob):
        _add(match.group(1))
    for item in task.get("files") or []:
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "write").strip().lower()
        if action in ("read", "context"):
            continue
        if action and action not in _WRITE_FILE_ACTIONS:
            continue
        _add(item.get("path"))
    if parent:
        for item in parent.get("files") or []:
            if isinstance(item, str):
                _add(item)
            elif isinstance(item, dict):
                _add(item.get("path"))
        for p in parent.get("writePaths") or []:
            _add(p)
    return ordered


def merge_architecture_related_files(task: Dict[str, Any]) -> List[str]:
    """Append ADR relatedFiles to writePaths (accepted first, then proposed)."""
    from backend.services.architecture_decisions import get_architecture_decisions

    added: List[str] = []
    existing = {str(p).strip() for p in (task.get("writePaths") or []) if str(p).strip()}
    ordered: List[str] = [str(p).strip() for p in (task.get("writePaths") or []) if str(p).strip()]
    for decision in get_architecture_decisions(task):
        status = str(decision.get("status") or "")
        if status not in ("accepted", "proposed"):
            continue
        for raw in decision.get("relatedFiles") or []:
            p = str(raw or "").strip().replace("\\", "/")
            if not p or p in existing:
                continue
            existing.add(p)
            ordered.append(p)
            added.append(p)
    if ordered:
        task["writePaths"] = ordered[:12]
    return added


def stamp_write_paths_on_po_task(
    task: Dict[str, Any],
    *,
    parent: Optional[Dict[str, Any]] = None,
    max_paths: int = 8,
) -> List[str]:
    """Merge inferred paths into task writePaths; return newly added paths."""
    merge_architecture_related_files(task)
    inferred = infer_write_paths_for_po_task(task, parent=parent)
    if not inferred:
        from backend.services.file_blocker import infer_feature_edit_path_from_title

        feat = infer_feature_edit_path_from_title(
            str(task.get("title") or ""),
            str(task.get("description") or ""),
        )
        if feat:
            inferred = [feat]
    if not inferred:
        return []
    existing = [str(p).strip() for p in (task.get("writePaths") or []) if str(p).strip()]
    merged = list(dict.fromkeys([*inferred, *existing]))[: max(1, int(max_paths))]
    added = [p for p in merged if p not in existing]
    task["writePaths"] = merged
    return added


def ensure_dev_write_paths_before_step(task_id: str) -> List[str]:
    """Stamp writePaths on the live board task before a Developer step."""
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(task_id))
    if not isinstance(task, dict):
        return []
    try:
        from backend.services.dev_card_recovery import ensure_lint_source_for_feature_task

        ensure_lint_source_for_feature_task(task)
    except Exception:
        pass
    parent_id = str(task.get("parentTaskId") or task.get("parentId") or "")
    parent = find_task_by_id(parent_id) if parent_id else None
    return stamp_write_paths_on_po_task(task, parent=parent if isinstance(parent, dict) else None)


def build_edit_target_system_block(task: Optional[Dict[str, Any]]) -> str:
    """System text naming the primary edit file for Dev."""
    if not isinstance(task, dict):
        return ""
    from backend.services.file_blocker import resolve_dev_edit_target_path

    target = str(resolve_dev_edit_target_path(task) or "").strip()
    paths = [str(p).strip() for p in (task.get("writePaths") or []) if str(p).strip()]
    primary = target or (paths[0] if paths else "")
    if not primary:
        return ""
    extra = ""
    if len(paths) > 1:
        extra = f" Secondary paths: {', '.join(f'`{p}`' for p in paths[1:4])}."
    return (
        f"=== EDIT TARGET ===\n"
        f"Primary file: `{primary}`.{extra}\n"
        "First tool call must be read_file on the primary file (or apply_patch only after "
        "read_file on that path this step). Do not edit pubspec.yaml or README unless they "
        "are the primary target."
    )


def maybe_queue_oversize_split(task: Dict[str, Any]) -> bool:
    """Flag cards that are too large for one local Dev turn."""
    if not isinstance(task, dict) or task.get("pendingSplit"):
        return False
    from backend.services.workflow_settings import get_workflow_settings

    if not get_workflow_settings().get("enableSplitOnStuck", True):
        return False
    ac = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    paths = [str(p).strip() for p in (task.get("writePaths") or []) if str(p).strip()]
    if len(ac) > 2 or len(paths) > 1:
        task["pendingSplit"] = True
        task["pendingSplitReason"] = "po_oversize_guard"
        return True
    return False
