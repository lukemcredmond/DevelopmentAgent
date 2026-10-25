"""Auto-recover high-churn Developer cards (lint target, latch, patch loops)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.logs import add_system_log


def _screen_feature_title(task: Dict[str, Any]) -> bool:
    blob = f"{task.get('title') or ''} {task.get('description') or ''}".lower()
    return any(
        hint in blob
        for hint in (
            "shopping list",
            "meal library",
            "meallibrary",
            "navigation",
            " empty state",
            "list ui",
            "screen",
        )
    )


def ensure_lint_source_for_feature_task(task: Dict[str, Any]) -> bool:
    """Set lintSourceFile from workspace/title when missing or stuck on lib/main.dart."""
    from backend.services.file_blocker import (
        infer_feature_edit_path_from_title,
        normalize_file_path,
        validate_dev_edit_target_path,
    )

    if not isinstance(task, dict):
        return False
    feat = infer_feature_edit_path_from_title(
        str(task.get("title") or ""),
        str(task.get("description") or ""),
    )
    if not feat or not validate_dev_edit_target_path(feat):
        return False
    current = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if current == feat:
        return False
    if not current or current in ("(unknown)", "__overflow__"):
        task["lintSourceFile"] = feat
        return True
    if current == "lib/main.dart" and _screen_feature_title(task) and feat != current:
        task["lintSourceFile"] = feat
        return True
    return False


def auto_unstick_high_churn_dev_card(task_id: str) -> bool:
    """Repair edit target / defer or split before another doomed Dev visit."""
    live = find_task_by_id(str(task_id or ""))
    if not live or not isinstance(live, dict):
        return False

    changed = False
    lrd = live.get("lastRefusalDiagnosis") or {}
    cause = str(lrd.get("cause") or "")
    patch_fails = int(live.get("identicalPatchFailCount") or 0)
    last_exit = str((live.get("lastStepOutcome") or {}).get("exitReason") or "")

    if cause == "missing_edit_target" or (
        patch_fails >= 2 and _screen_feature_title(live)
    ):
        if ensure_lint_source_for_feature_task(live):
            live["identicalPatchFailCount"] = 0
            live.pop("devDeferredUntilStep", None)
            live["consecutiveNoWriteStall"] = 0
            changed = True
            add_system_log(
                "System",
                "info",
                f"{task_id}: set lintSourceFile to `{live.get('lintSourceFile')}` "
                "from feature title (missing edit target / patch churn)",
            )
            record_task_decision(
                task_id,
                "System",
                "lint_source_repair",
                str(live.get("lintSourceFile") or ""),
            )

    if last_exit == "dev_step_wall" and cause == "missing_edit_target" and not changed:
        ensure_lint_source_for_feature_task(live)
        if live.get("lintSourceFile"):
            from backend.services.sprint_speed_gates import reset_dev_cycle_latch

            reset_dev_cycle_latch(live)
            live.pop("devDeferredUntilStep", None)
            changed = True
            add_system_log(
                "System",
                "info",
                f"{task_id}: reset Dev latch after dev_step_wall + lintSourceFile repair",
            )

    if patch_fails >= 4 or (
        last_exit in ("dev_step_wall", "max_iterations") and patch_fails >= 3
    ):
        from backend.services.sprint_service import (
            _run_stuck_auto_split,
            _should_attempt_stuck_auto_split,
        )
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
        max_stuck = int(ws.get("maxStuckSteps", 3) or 3)
        if _should_attempt_stuck_auto_split(live, ws):
            if _run_stuck_auto_split(task_id, live, max_stuck):
                changed = True
                add_system_log(
                    "System",
                    "warning",
                    f"{task_id}: auto-split after high identicalPatchFailCount ({patch_fails})",
                )

    if changed:
        try:
            from backend.services.project_service import save_current_project_state

            save_current_project_state(persist_board=True)
        except Exception:
            pass
    return changed
