"""Support bundle export for debugging."""

from fastapi import APIRouter
from fastapi.responses import Response

from backend import state
from backend.services.support_bundle import build_support_bundle_bytes, support_bundle_filename

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
