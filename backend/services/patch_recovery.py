"""Post-rewind recovery after failed apply_patch: must read_file then retry."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

PATCH_RECOVERY_MARKER = "=== PATCH RECOVERY ==="
PATCH_RECOVERY_REMINDER_MARKER = "=== PATCH RECOVERY (blocked) ==="


def normalize_recovery_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


def failed_apply_patch_paths(
    batch: Sequence[Tuple[str, Dict[str, Any], Any]],
) -> List[str]:
    """Extract unique paths from failed apply_patch results in a tool batch."""
    paths: List[str] = []
    seen: Set[str] = set()
    for name, args, result in batch:
        if name != "apply_patch":
            continue
        if bool(getattr(result, "success", False)):
            continue
        path = normalize_recovery_path(str((args or {}).get("path") or ""))
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def build_patch_recovery_nudge(paths: Iterable[str]) -> str:
    plist = ", ".join(f"`{normalize_recovery_path(p)}`" for p in paths if normalize_recovery_path(p))
    if not plist:
        plist = "(unknown path)"
    return (
        f"{PATCH_RECOVERY_MARKER}\n"
        f"apply_patch failed on: {plist}.\n"
        "Next tool call MUST be read_file on that path (fresh this step).\n"
        "Then apply_patch using verbatim old_text from that read_file output.\n"
        "Do NOT retry apply_patch with the same old_text / preloaded context.\n"
        "write_file cannot overwrite existing files until patch escalation allows it."
    )


def build_patch_recovery_reminder(paths: Iterable[str]) -> str:
    plist = ", ".join(f"`{normalize_recovery_path(p)}`" for p in paths if normalize_recovery_path(p))
    return (
        f"{PATCH_RECOVERY_REMINDER_MARKER}\n"
        f"read_file required first for: {plist}.\n"
        "You called apply_patch again without a fresh read_file on that path. "
        "Call read_file now, then apply_patch with verbatim old_text from the tool output."
    )


def paths_needing_read_before_patch(
    pending: Set[str],
    read_satisfied: Set[str],
    batch: Sequence[Tuple[str, Dict[str, Any], Any]],
) -> List[str]:
    """
    Pending recovery paths that received apply_patch without a successful
    read_file since recovery (or earlier in this batch).
    """
    pending_norm = {normalize_recovery_path(p) for p in pending if normalize_recovery_path(p)}
    if not pending_norm:
        return []
    read_so_far = {normalize_recovery_path(p) for p in read_satisfied if normalize_recovery_path(p)}
    offenders: List[str] = []
    offender_seen: Set[str] = set()
    for name, args, result in batch:
        path = normalize_recovery_path(str((args or {}).get("path") or ""))
        if not path:
            continue
        if name == "read_file" and bool(getattr(result, "success", False)):
            read_so_far.add(path)
        elif name == "apply_patch" and path in pending_norm and path not in read_so_far:
            if path not in offender_seen:
                offender_seen.add(path)
                offenders.append(path)
    return offenders


def apply_batch_to_recovery_state(
    pending: Set[str],
    read_satisfied: Set[str],
    batch: Sequence[Tuple[str, Dict[str, Any], Any]],
) -> Tuple[Set[str], Set[str]]:
    """
    Update recovery sets after a batch:
    - successful read_file on a pending path → mark read_satisfied
    - successful apply_patch/write_file → clear path from pending + read_satisfied
    - failed apply_patch → ensure path is in pending (caller may also set)
    """
    next_pending = {normalize_recovery_path(p) for p in pending if normalize_recovery_path(p)}
    next_read = {normalize_recovery_path(p) for p in read_satisfied if normalize_recovery_path(p)}
    for name, args, result in batch:
        path = normalize_recovery_path(str((args or {}).get("path") or ""))
        if not path:
            continue
        ok = bool(getattr(result, "success", False))
        if name == "read_file" and ok and path in next_pending:
            next_read.add(path)
        elif name in ("apply_patch", "write_file") and ok:
            next_pending.discard(path)
            next_read.discard(path)
        elif name == "apply_patch" and not ok:
            next_pending.add(path)
            # Stale read no longer counts after another failed patch.
            next_read.discard(path)
    return next_pending, next_read


def failed_patch_fingerprint(path: str, old_text: str = "", new_text: str = "") -> str:
    """Fingerprint identical apply_patch attempts (path + old/new text)."""
    from backend.services.sprint_speed_gates import patch_fingerprint

    return patch_fingerprint(path, old_text=old_text or "", summary=(new_text or "")[:200])


def build_patch_fail_excerpt_nudge(path: str, file_content: str, old_text: str) -> str:
    """Inject a file excerpt around the failed old_text to avoid an extra read round-trip."""
    path_norm = normalize_recovery_path(path)
    lines = str(file_content or "").splitlines()
    old_lines = [ln for ln in str(old_text or "").splitlines() if ln.strip()]
    anchor = old_lines[0].strip() if old_lines else ""
    center = 0
    if anchor:
        for i, line in enumerate(lines):
            if anchor in line:
                center = i
                break
    start = max(0, center - 20)
    end = min(len(lines), center + 20)
    excerpt = "\n".join(f"  {i + 1}| {lines[i]}" for i in range(start, end))
    return (
        f"{PATCH_RECOVERY_MARKER}\n"
        f"apply_patch failed on `{path_norm}`. Current file excerpt (lines {start + 1}-{end}):\n"
        f"{excerpt}\n"
        "Copy old_text verbatim from this excerpt or call read_file, then apply_patch."
    )


def build_write_file_escalation_nudge(
    paths: Iterable[str],
    *,
    overwrite_allowed: bool = True,
) -> str:
    plist = ", ".join(f"`{normalize_recovery_path(p)}`" for p in paths if normalize_recovery_path(p))
    if not plist:
        plist = "(unknown path)"
    if overwrite_allowed:
        overwrite_line = (
            f"write_file overwrite IS allowed for {plist} this step — "
            "call read_file for the full file, then write_file with complete corrected content.\n"
        )
    else:
        overwrite_line = (
            "Use read_file then apply_patch with a different old_text — "
            "write_file overwrite is not enabled this step.\n"
        )
    return (
        f"{PATCH_RECOVERY_MARKER}\n"
        f"Identical apply_patch failed repeatedly on: {plist}.\n"
        "Do NOT retry the same apply_patch.\n"
        + overwrite_line
    )


def compute_write_overwrite_allowed_paths(
    pending: Iterable[str],
    escalated: Iterable[str],
    task: Optional[Dict[str, Any]] = None,
) -> Set[str]:
    """Paths where Developer may write_file over an existing file this step."""
    out: Set[str] = set()
    for raw in list(pending) + list(escalated):
        p = normalize_recovery_path(raw)
        if p:
            out.add(p)
    if isinstance(task, dict) and task.get("forcePatchNextDevStep"):
        try:
            from backend.services.file_blocker import resolve_dev_edit_target_path

            target = normalize_recovery_path(resolve_dev_edit_target_path(task) or "")
            if target:
                out.add(target)
        except Exception:
            pass
    return out
