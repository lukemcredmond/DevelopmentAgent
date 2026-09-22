"""Build a zip support bundle for debugging and sharing with agents."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backend import state
from backend.config import diagnostics_dir
from backend.services.workflow_settings import get_workflow_settings


def _git_branch(cwd: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        return out.strip()
    except Exception:
        return ""


def build_support_bundle_bytes(*, max_diagnostics: int = 30) -> bytes:
    """Return zip bytes for the current project state and recent diagnostics."""
    pid = str(state.CURRENT_PROJECT_ID or "default-proj")
    ws = get_workflow_settings()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("console_logs.json", json.dumps(list(state.SYSTEM_LOGS or []), indent=2))
        zf.writestr(
            "board_snapshot.json",
            json.dumps(state.SHARED_BOARD or {}, indent=2, default=str),
        )
        zf.writestr("workflow_settings.json", json.dumps(ws, indent=2, default=str))
        if isinstance(state.LAST_STEP_OUTCOME, dict):
            zf.writestr("last_step_outcome.json", json.dumps(state.LAST_STEP_OUTCOME, indent=2))
        if isinstance(state.LAST_STEP_DIAGNOSTICS, dict):
            zf.writestr(
                "last_step_diagnostics.json",
                json.dumps(state.LAST_STEP_DIAGNOSTICS, indent=2, default=str),
            )

        diag_root = diagnostics_dir(pid)
        diag_files: List[Path] = []
        if diag_root.is_dir():
            diag_files = sorted(
                diag_root.glob("step-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[: max(1, min(max_diagnostics, 100))]
        for path in diag_files:
            try:
                zf.writestr(f"diagnostics/{path.name}", path.read_text(encoding="utf-8"))
            except OSError:
                continue

        try:
            from backend.services.sprint_report import list_sprint_reports

            reports = list_sprint_reports(pid)[:10]
            if reports:
                zf.writestr("sprint_reports/index.json", json.dumps(reports, indent=2))
        except Exception:
            pass

        env_lines = [
            f"project_id={pid}",
            f"project_name={state.PROJECT_NAME}",
            f"workspace_dir={state.WORKSPACE_DIR}",
            f"active_sprint_task={state.ACTIVE_SPRINT_TASK_ID or ''}",
            f"active_sprint_agent={state.ACTIVE_SPRINT_AGENT or ''}",
            f"sprint_progress_step={state.SPRINT_PROGRESS_STEP}",
            f"sprint_progress_max={state.SPRINT_PROGRESS_MAX}",
            f"git_branch={_git_branch(str(state.WORKSPACE_DIR or '.'))}",
            f"exported_at={datetime.now(timezone.utc).isoformat()}",
            f"diagnostics_dir={diag_root}",
        ]
        zf.writestr("environment.txt", "\n".join(env_lines) + "\n")

    buffer.seek(0)
    return buffer.getvalue()


def support_bundle_filename() -> str:
    pid = str(state.CURRENT_PROJECT_ID or "project")[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"allhands-support-{pid}-{stamp}.zip"
