"""Shared-file blocker: one fix card per broken file, dependents in Blocked."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import (
    find_task_by_id,
    get_task_lane,
    normalize_task,
    record_task_decision,
)
from backend.services.lint_fanout import _build_file_card, is_junk_lint_path
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

BLOCKED_BY_KIND_FILE_FIX = "file_fix"
_OPEN_LANES_SKIP = frozenset({"Done"})
_DEPENDENT_LANES = (
    "In Progress",
    "Needs User",
    "Backlog",
    "Refinement",
    "Pending Approval",
    "QA",
    "Code Review",
    "Blocked",
    "Needs PO",
)


def normalize_file_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


_VALID_DEV_EDIT_PATH_RE = re.compile(r"^[\w./-]+$")
_MAX_DEV_EDIT_PATH_LEN = 260


def _is_low_priority_doc_path(path: str) -> bool:
    """README and similar docs are poor default edit targets for feature Dev steps."""
    norm = normalize_file_path(path).lower()
    base = norm.rsplit("/", 1)[-1]
    return base in (
        "readme.md",
        "changelog.md",
        "license",
        "license.md",
        "contributing.md",
    )


def _scaffold_edit_path_from_title(title: str, description: str = "") -> str:
    combined = f"{title} {description}".lower()
    hints = (
        "scaffold",
        "offline",
        "persistence",
        "main.dart",
        "startup",
        "edge case",
    )
    if not any(h in combined for h in hints):
        return ""
    root = Path(str(state.WORKSPACE_DIR or "")).expanduser()
    main = root / "lib" / "main.dart"
    if main.is_file():
        return "lib/main.dart"
    return ""


def workspace_looks_like_flutter_dart() -> bool:
    """True when workspace pubspec looks like a Flutter/Dart app."""
    root = Path(str(state.WORKSPACE_DIR or "")).expanduser()
    pub = root / "pubspec.yaml"
    if not pub.is_file():
        return False
    try:
        text = pub.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "flutter:" in text or "sdk:flutter" in text


def validate_dev_edit_target_path(path: str) -> bool:
    """True when path looks like a workspace-relative file, not a lint diagnostic line."""
    raw = normalize_file_path(path)
    if not raw or len(raw) > _MAX_DEV_EDIT_PATH_LEN:
        return False
    lower = raw.lower()
    if "•" in raw or "warning" in lower or raw.startswith("Error:"):
        return False
    if not _VALID_DEV_EDIT_PATH_RE.match(raw):
        return False
    if is_junk_lint_path(raw):
        return False
    if workspace_looks_like_flutter_dart() and raw.endswith(".py"):
        return False
    return True


def _extract_path_from_diagnostic_line(line: str) -> str:
    """Extract trailing filename from formatted lint lines."""
    raw = str(line or "").strip()
    if "•" in raw:
        tail = normalize_file_path(raw.rsplit("•", 1)[-1])
        if validate_dev_edit_target_path(tail):
            return tail
    if validate_dev_edit_target_path(raw):
        return raw
    return ""


def _title_derived_edit_path(title: str) -> str:
    title = str(title or "")
    lower = title.lower()
    for hint in ("pubspec.yaml", "analysis_options.yaml"):
        if hint in lower:
            return hint
    if "main.dart" in lower or "main app" in lower:
        return "lib/main.dart"
    lib_path = re.search(r"\blib/([\w./-]+)\b", title, re.I)
    if lib_path:
        segment = normalize_file_path(lib_path.group(0))
        if not re.search(r"\.(?:dart|yaml)$", segment, re.I):
            dart_candidate = f"{segment}.dart"
            if validate_dev_edit_target_path(dart_candidate):
                return dart_candidate
    match = re.search(r"(lib/[\w./-]+\.(?:dart|yaml))", title, re.I)
    if match:
        candidate = normalize_file_path(match.group(1))
        if validate_dev_edit_target_path(candidate):
            return candidate
    feat = infer_feature_edit_path_from_title(title, "")
    return feat


def infer_feature_edit_path_from_title(title: str, description: str = "") -> str:
    """Best-effort lib/*.dart path from feature titles (meal library, shopping list, …)."""
    combined = f"{title} {description}".lower()
    keywords: List[str] = []
    if "meal library" in combined or ("meal" in combined and "library" in combined):
        keywords.extend(["meal_library", "meals", "meal"])
    if "shopping list" in combined or ("shopping" in combined and "list" in combined):
        keywords.extend(["shopping_list", "shopping"])
    if "meal library" in combined or "meallibrary" in combined.replace(" ", ""):
        keywords.extend(["meal_library_screen", "meal_library", "meals"])
    if "navigation" in combined and ("meal" in combined or "library" in combined):
        keywords.extend(["meal_library", "main"])
    if "settings" in combined and ("provider" in combined or "screen" in combined):
        keywords.extend(["settings"])
    if not keywords:
        return ""
    from backend import state

    root = Path(str(state.WORKSPACE_DIR or "")).expanduser()
    lib_dir = root / "lib"
    if not lib_dir.is_dir():
        return ""
    candidates: List[str] = []
    try:
        for path in lib_dir.rglob("*.dart"):
            rel = path.relative_to(root).as_posix()
            lower = rel.lower()
            if any(k in lower for k in keywords):
                candidates.append(rel)
    except OSError:
        return ""
    if not candidates:
        return ""
    candidates.sort(key=lambda p: (p.count("/"), len(p)))
    for rel in candidates:
        if validate_dev_edit_target_path(rel):
            return rel
    return ""


def infer_lint_source_file(task: Dict[str, Any]) -> str:
    """Set lintSourceFile from diagnostics when missing (e.g. flutter_lints URI errors)."""
    if not isinstance(task, dict):
        return ""
    existing = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if existing and existing not in ("(unknown)", "__overflow__"):
        return existing
    blob = " ".join(
        str(d.get(field) or "")
        for d in (task.get("lastCommandDiagnostics") or [])
        if isinstance(d, dict)
        for field in ("file", "path", "message")
    )
    lower = blob.lower()
    if "package:flutter_lints/flutter.yaml" in lower or "analysis_options.yaml" in lower:
        return "analysis_options.yaml"
    if "pubspec.yaml" in lower:
        return "pubspec.yaml"
    title_lower = str(task.get("title") or "").lower()
    if "undefined class" in lower and "widget" in lower:
        if "main.dart" in lower or "main.dart" in title_lower:
            return "lib/main.dart"
    return ""


def resolve_dev_edit_target_path(task: Dict[str, Any]) -> str:
    """Resolve a safe workspace file for synthetic read / text-loop hints."""
    if not isinstance(task, dict):
        return ""
    src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if src and src not in ("(unknown)", "__overflow__") and validate_dev_edit_target_path(src):
        return src
    title = str(task.get("title") or "")
    if title.startswith("Lint: "):
        path = normalize_file_path(title[6:])
        if validate_dev_edit_target_path(path):
            return path
    description = str(task.get("description") or "")
    feat_early = infer_feature_edit_path_from_title(title, description)
    if feat_early and validate_dev_edit_target_path(feat_early):
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = feat_early
        return feat_early
    scaffold_path = _scaffold_edit_path_from_title(title, description)
    if scaffold_path and validate_dev_edit_target_path(scaffold_path):
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = scaffold_path
        return scaffold_path
    path_candidates: List[str] = []
    for key in ("writePaths", "scaffoldedFiles"):
        for raw in task.get(key) or []:
            path = normalize_file_path(str(raw or ""))
            if validate_dev_edit_target_path(path):
                path_candidates.append(path)
    code_paths = [p for p in path_candidates if not _is_low_priority_doc_path(p)]
    if code_paths:
        chosen = code_paths[0]
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = chosen
        return chosen
    if path_candidates:
        chosen = path_candidates[0]
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = chosen
        return chosen
    inferred = infer_lint_source_file(task)
    if inferred and validate_dev_edit_target_path(inferred):
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = inferred
        return inferred
    for diagnostic in reversed(task.get("lastCommandDiagnostics") or []):
        if not isinstance(diagnostic, dict):
            continue
        for field in ("file", "path"):
            raw = str(diagnostic.get(field) or "")
            if validate_dev_edit_target_path(raw):
                return normalize_file_path(raw)
            extracted = _extract_path_from_diagnostic_line(raw)
            if extracted:
                return extracted
    derived = _title_derived_edit_path(title)
    if derived and validate_dev_edit_target_path(derived):
        if not task.get("lintSourceFile"):
            task["lintSourceFile"] = derived
        return derived
    feat = infer_feature_edit_path_from_title(title, str(task.get("description") or ""))
    if feat and validate_dev_edit_target_path(feat) and not task.get("lintSourceFile"):
        task["lintSourceFile"] = feat
    return feat


def _integration_edit_paths(task: Dict[str, Any]) -> List[str]:
    if not isinstance(task, dict):
        return []
    paths: List[str] = []
    for raw in task.get("integrationEditPaths") or []:
        p = normalize_file_path(str(raw or ""))
        if p and validate_dev_edit_target_path(p):
            paths.append(p)
    return paths


def path_in_integration_edit_scope(task: Dict[str, Any], path: str) -> bool:
    norm = normalize_file_path(path)
    if not norm:
        return False
    for scoped in _integration_edit_paths(task):
        if scoped == norm:
            return True
        if scoped.endswith("/**"):
            prefix = scoped[:-3]
            if norm.startswith(prefix):
                return True
    return False


def dev_edit_path_explicitly_allowed(task: Dict[str, Any], path: str) -> bool:
    """True when path is in scope even if it differs from resolve_dev_edit_target_path."""
    norm = normalize_file_path(path)
    if not norm:
        return False
    if path_in_integration_edit_scope(task, norm):
        return True
    fix_for = normalize_file_path(str(task.get("fileFixFor") or ""))
    if fix_for and fix_for == norm:
        return True
    title = str(task.get("title") or "").lower()
    if norm == "pubspec.yaml" and ("pubspec" in title or "sdk constraint" in title):
        return True
    if norm == "analysis_options.yaml" and "analysis_options" in title:
        return True
    scoped_src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if scoped_src and scoped_src == norm:
        return True
    return False


def dev_read_path_allowed_for_tool(task: Dict[str, Any], path: str) -> tuple[bool, str]:
    """Gate Developer read_file on Flutter cards to Dart sources and scoped targets."""
    norm = normalize_file_path(path)
    if not norm:
        return False, f"Invalid or missing read path: {path or '(empty)'}"
    if workspace_looks_like_flutter_dart():
        if (
            norm.endswith(".py")
            or norm.startswith("app/")
            or norm.startswith("backend/")
            or "billing.py" in norm.lower()
        ):
            return (
                False,
                f"Flutter Dev: `{norm}` is outside Dart scope — use lintSourceFile or lib/**/*.dart.",
            )
    if not validate_dev_edit_target_path(norm):
        return False, f"Invalid or missing read path: {path or '(empty)'}"
    if not workspace_looks_like_flutter_dart():
        return True, ""
    if dev_edit_path_explicitly_allowed(task, norm):
        return True, ""
    target = normalize_file_path(resolve_dev_edit_target_path(task) or "")
    if target and target == norm:
        return True, ""
    if norm in ("pubspec.yaml", "analysis_options.yaml"):
        return True, ""
    if norm.endswith(".dart") and norm.startswith(("lib/", "test/", "integration_test/")):
        return True, ""
    if _is_low_priority_doc_path(norm):
        return (
            False,
            f"Flutter Dev: do not read `{norm}` — open lintSourceFile or the target Dart file.",
        )
    return True, ""


def dev_edit_path_allowed_for_tool(task: Dict[str, Any], path: str) -> tuple[bool, str]:
    """Gate Developer apply_patch/write_file to the card's resolved edit target."""
    norm = normalize_file_path(path)
    if not norm or not validate_dev_edit_target_path(norm):
        return False, f"Invalid or missing edit path: {path or '(empty)'}"
    if workspace_looks_like_flutter_dart():
        allowed, err = dev_read_path_allowed_for_tool(task, norm)
        if not allowed:
            return False, err
    integration = _integration_edit_paths(task)
    if integration and path_in_integration_edit_scope(task, norm):
        return True, ""
    target = resolve_dev_edit_target_path(task)
    if not target:
        return True, ""
    if normalize_file_path(target) == norm:
        return True, ""
    if dev_edit_path_explicitly_allowed(task, norm):
        return True, ""
    return (
        False,
        f"This card is scoped to `{target}`; do not edit `{norm}`. "
        f"Call read_file on `{target}`, then apply_patch with verbatim old_text.",
    )


def is_file_blocker_wait(task: Dict[str, Any]) -> bool:
    return str(task.get("blockedByKind") or "") == BLOCKED_BY_KIND_FILE_FIX


def _task_file_path(task: Dict[str, Any]) -> str:
    src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if src and src not in ("(unknown)", "__overflow__"):
        return src
    title = str(task.get("title") or "")
    if title.startswith("Lint: "):
        path = normalize_file_path(title[6:])
        if path and not path.startswith("overflow"):
            return path
    for d in task.get("lastCommandDiagnostics") or []:
        if isinstance(d, dict):
            f = normalize_file_path(str(d.get("file") or ""))
            if f and not is_junk_lint_path(f):
                return f
    for p in task.get("writePaths") or []:
        f = normalize_file_path(str(p or ""))
        if f:
            return f
    return ""


def _task_touches_file(task: Dict[str, Any], path: str) -> bool:
    norm = normalize_file_path(path)
    if not norm:
        return False
    if normalize_file_path(_task_file_path(task)) == norm:
        return True
    title = str(task.get("title") or "")
    if title == f"Lint: {norm}":
        return True
    for d in task.get("lastCommandDiagnostics") or []:
        if isinstance(d, dict) and normalize_file_path(str(d.get("file") or "")) == norm:
            return True
    for p in task.get("writePaths") or []:
        if normalize_file_path(str(p or "")) == norm:
            return True
    return False


def _iter_open_tasks() -> List[Tuple[str, Dict[str, Any]]]:
    found: List[Tuple[str, Dict[str, Any]]] = []
    for lane, tasks in (state.SHARED_BOARD or {}).items():
        if lane in _OPEN_LANES_SKIP:
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, dict):
                found.append((lane, task))
    return found


def _find_fix_card(path: str) -> Optional[Dict[str, Any]]:
    norm = normalize_file_path(path)
    explicit: Optional[Dict[str, Any]] = None
    lint_title: Optional[Dict[str, Any]] = None
    fallback: Optional[Dict[str, Any]] = None
    for _lane, task in _iter_open_tasks():
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if normalize_file_path(str(task.get("fileFixFor") or "")) == norm:
            explicit = task
            break
        title = str(task.get("title") or "")
        if title == f"Lint: {norm}":
            if lint_title is None:
                lint_title = task
        elif normalize_file_path(_task_file_path(task)) == norm:
            if fallback is None:
                fallback = task
    return explicit or lint_title or fallback


def _collect_fix_card_scope_paths(
    broken_path: str,
    touching: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
) -> List[str]:
    paths: set[str] = set()
    primary = normalize_file_path(broken_path)
    if primary:
        paths.add(primary)
    for _lane, task in touching or []:
        if not isinstance(task, dict):
            continue
        for key in ("lintSourceFile", "fileFixFor"):
            p = normalize_file_path(str(task.get(key) or ""))
            if p and p not in ("(unknown)", "__overflow__"):
                paths.add(p)
        for raw in task.get("writePaths") or []:
            p = normalize_file_path(str(raw or ""))
            if p:
                paths.add(p)
    code_paths = sorted(
        p
        for p in paths
        if p.startswith(("lib/", "test/"))
        or p.endswith((".dart", ".yaml"))
        or p == "pubspec.yaml"
    )
    if code_paths:
        if "README.md" in paths:
            paths.discard("README.md")
        ordered = [p for p in code_paths if p in paths]
        for p in sorted(paths):
            if p not in ordered:
                ordered.append(p)
        return ordered[:12]
    return sorted(paths)[:12]


def _create_fix_card_payload(
    path: str,
    diagnostics: Optional[List[Dict[str, Any]]],
    source_task_id: str,
    touching: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    scope_paths = _collect_fix_card_scope_paths(path, touching)
    norm = normalize_file_path(path)
    if norm == "README.md" and any(p.startswith("lib/") for p in scope_paths):
        for p in scope_paths:
            if p.startswith("lib/"):
                norm = p
                break
    diags: List[Dict[str, Any]] = []
    if diagnostics:
        diags = [
            d
            for d in diagnostics
            if isinstance(d, dict)
            and normalize_file_path(str(d.get("file") or "")) == norm
        ]
    if not diags:
        diags = [
            {
                "file": norm,
                "line": 0,
                "column": 0,
                "severity": "error",
                "message": "Fix diagnostics for this file",
            }
        ]
    card = _build_file_card(source_task_id or "file-blocker", norm, diags)
    card["priority"] = 1
    card["fileFixFor"] = norm
    card["lintSourceFile"] = norm
    if scope_paths:
        card["writePaths"] = scope_paths
        if len(scope_paths) >= 2:
            card["integrationEditPaths"] = scope_paths
    return card


def _clear_needs_user_fields(task: Dict[str, Any]) -> None:
    task["userQuestion"] = None
    task["needsUserReason"] = None
    task["needsUserAction"] = None
    task["needsUserKind"] = None
    task["needsUserSuggestedTarget"] = None
    task["needsUserOptions"] = None
    task["needsUserDuplicate"] = False


def _wire_dependent(task: Dict[str, Any], fix_id: str) -> bool:
    tid = str(task.get("id") or "")
    if not tid or tid == fix_id:
        return False
    lane = get_task_lane(tid) or str(task.get("status") or "Backlog")
    if lane == "Done":
        return False
    blocked = [str(x) for x in (task.get("blockedBy") or []) if x]
    if fix_id in blocked:
        return False
    blocked.append(fix_id)
    task["blockedBy"] = blocked
    task["blockedByKind"] = BLOCKED_BY_KIND_FILE_FIX
    if lane != "Blocked":
        task["blockedReturnLane"] = lane
    _clear_needs_user_fields(task)
    record_task_decision(
        tid,
        "System",
        "file_blocker",
        f"Blocked on fix card {fix_id} for shared file",
    )
    return True


def _link_related(source_task_id: str, fix_id: str) -> None:
    if not source_task_id or not fix_id:
        return
    parent = find_task_by_id(source_task_id)
    fix = find_task_by_id(fix_id)
    if parent:
        rel = list(parent.get("relatedTaskIds") or [])
        if fix_id not in rel:
            rel.append(fix_id)
            parent["relatedTaskIds"] = rel
    if fix:
        rel = list(fix.get("relatedTaskIds") or [])
        if source_task_id not in rel:
            rel.append(source_task_id)
            fix["relatedTaskIds"] = rel


def orchestrate_file_blocker(
    *,
    file_path: str,
    diagnostics: list[dict] | None = None,
    source_task_id: str = "",
    reason: str = "",
) -> dict:
    """Find or create one fix card and block dependents touching the same file."""
    ws = get_workflow_settings()
    if not ws.get("enableFileBlockerOrchestration", True):
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "disabled"}

    path = normalize_file_path(file_path)
    if not path or path in ("(unknown)", "__overflow__") or is_junk_lint_path(path):
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "invalid_path"}

    min_cards = max(1, int(ws.get("fileBlockerMinDependents", 2)))
    auto_create = bool(ws.get("fileBlockerAutoCreateFixCard", True))

    create_payload: Optional[Dict[str, Any]] = None
    with state.STATE_LOCK:
        touching = [(lane, t) for lane, t in _iter_open_tasks() if _task_touches_file(t, path)]
        fix = _find_fix_card(path)
        fix_id = str(fix.get("id") or "") if fix else ""
        if not fix_id and auto_create:
            create_payload = _create_fix_card_payload(
                path,
                diagnostics,
                source_task_id,
                touching=touching,
            )

    created = False
    if create_payload:
        from backend.services.board_service import append_backlog_tasks

        append_backlog_tasks([create_payload])
        created = True

    blocked_ids: List[str] = []
    skipped = ""
    with state.STATE_LOCK:
        fix = _find_fix_card(path)
        if not fix and auto_create:
            skipped = "no_fix_card"
            fix_id = ""
        elif not fix:
            skipped = "no_fix_card"
            fix_id = ""
        else:
            fix_id = str(fix.get("id") or "")
            fix["fileFixFor"] = path
            fix["lintSourceFile"] = path
            normalize_task(fix)

        open_for_file: List[Dict[str, Any]] = [t for _lane, t in touching if isinstance(t, dict)]
        if fix and fix not in open_for_file:
            open_for_file.append(fix)

        if not fix_id:
            return {"fixTaskId": "", "blocked": [], "created": created, "skipped": skipped or "no_fix_card"}

        if len(open_for_file) < min_cards:
            _link_related(source_task_id, fix_id)
            return {
                "fixTaskId": fix_id,
                "blocked": [],
                "created": created,
                "skipped": "below_min_dependents",
            }

        for _lane, task in touching:
            tid = str(task.get("id") or "")
            if tid == fix_id:
                continue
            if _wire_dependent(task, fix_id):
                blocked_ids.append(tid)

        _link_related(source_task_id, fix_id)

        if blocked_ids:
            from backend.services.blocked_lane import sync_blocked_lane

            sync_blocked_lane(persist=True)
            detail = reason or f"Shared file {path}"
            add_system_log(
                "System",
                "info",
                f"File blocker {path}: fix {fix_id}, blocked {len(blocked_ids)} card(s) — {detail}",
            )
            record_task_decision(
                fix_id,
                "System",
                "file_blocker",
                f"Canonical fix card for {path}; {len(blocked_ids)} dependent(s) blocked",
                detail[:500],
            )

    return {
        "fixTaskId": fix_id,
        "blocked": blocked_ids,
        "created": created,
        "skipped": skipped,
    }


def orchestrate_from_task(task: Dict[str, Any], *, reason: str = "") -> dict:
    path = _task_file_path(task)
    if not path:
        return {"fixTaskId": "", "blocked": [], "created": False, "skipped": "no_file"}
    diags = task.get("lastCommandDiagnostics")
    return orchestrate_file_blocker(
        file_path=path,
        diagnostics=diags if isinstance(diags, list) else None,
        source_task_id=str(task.get("id") or ""),
        reason=reason,
    )


def _is_lint_pile_candidate(task: Dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    path = _task_file_path(task)
    if not path or is_junk_lint_path(path):
        return False
    title = str(task.get("title") or "")
    if title.startswith("Lint: ") or task.get("lintSourceFile"):
        return True
    diags = task.get("lastCommandDiagnostics") or []
    return isinstance(diags, list) and len(diags) > 0


def reconcile_file_blockers_from_board() -> dict:
    """Group lint pile cards by file, create one fix card each, block dependents."""
    ws = get_workflow_settings()
    if not ws.get("enableFileBlockerOrchestration", True):
        return {"files": 0, "blocked": 0, "fixCards": 0, "skipped": "disabled"}

    min_cards = max(1, int(ws.get("fileBlockerMinDependents", 2)))
    groups: Dict[str, List[str]] = defaultdict(list)

    with state.STATE_LOCK:
        for lane in _DEPENDENT_LANES:
            for task in list(state.SHARED_BOARD.get(lane, [])):
                if not _is_lint_pile_candidate(task):
                    continue
                path = _task_file_path(task)
                tid = str(task.get("id") or "")
                if path and tid:
                    groups[path].append(tid)

    files_processed = 0
    total_blocked = 0
    fix_cards = 0
    for path, tids in groups.items():
        if len(tids) < min_cards:
            continue
        result = orchestrate_file_blocker(
            file_path=path,
            source_task_id=tids[0],
            reason="reconcile lint pile",
        )
        if result.get("fixTaskId"):
            fix_cards += 1
        blocked = result.get("blocked") or []
        if blocked:
            total_blocked += len(blocked)
            files_processed += 1

    if total_blocked:
        add_system_log(
            "System",
            "success",
            f"Reconciled lint pile: {files_processed} file(s), "
            f"{total_blocked} card(s) blocked, {fix_cards} fix card(s)",
        )

    return {
        "files": files_processed,
        "blocked": total_blocked,
        "fixCards": fix_cards,
        "skipped": "",
    }
