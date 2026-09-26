"""Support bundle export for debugging."""

from fastapi import APIRouter
from fastapi.responses import Response

from backend import state
from backend.services.support_bundle import build_support_bundle_bytes, support_bundle_filename
from backend.services.sprint_diagnostics_rollup import build_sprint_diagnostics_rollup
from backend.services.board_duplicate_audit import duplicate_audit_summary

router = APIRouter()


@router.get("/api/support/bundle")
def download_support_bundle():
    data = build_support_bundle_bytes()
    filename = support_bundle_filename()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/support/console-tail")
def console_log_tail(limit: int = 50):
    n = max(1, min(int(limit or 50), 500))
    logs = list(state.SYSTEM_LOGS or [])[-n:]
    lines = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"{entry.get('source', 'System')} {entry.get('timestamp', '')} [{entry.get('type', 'info')}] "
            f"{entry.get('text', '')}"
        )
    return {"lines": lines, "text": "\n".join(lines)}


@router.get("/api/diagnostics/sprint-rollup")
def get_sprint_diagnostics_rollup(limit: int = 50, sinceHours: float = 72):
    with state.STATE_LOCK:
        pid = state.CURRENT_PROJECT_ID
    dup = duplicate_audit_summary()
    rollup = build_sprint_diagnostics_rollup(
        project_id=pid,
        limit=max(1, min(int(limit or 50), 200)),
        since_hours=max(1.0, min(float(sinceHours or 72), 720.0)),
        duplicate_summary=dup,
    )
    return rollup
