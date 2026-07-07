"""Qdrant URL and API key helpers from workflow settings."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.services.workflow_settings import get_workflow_settings


def qdrant_connection_settings(project_id: Optional[str] = None) -> tuple[str, Optional[str]]:
    ws = get_workflow_settings(project_id)
    url = str(ws.get("qdrantUrl") or "http://localhost:6333").rstrip("/")
    key = str(ws.get("qdrantApiKey") or "").strip() or None
    return url, key


def qdrant_request_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    if not api_key:
        return {}
    return {"api-key": api_key}


def sanitize_workflow_settings_for_client(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Strip secrets before sending workflow settings to the frontend."""
    out = dict(settings)
    key = str(out.get("qdrantApiKey") or "").strip()
    out["qdrantApiKeyConfigured"] = bool(key)
    out.pop("qdrantApiKey", None)
    return out
