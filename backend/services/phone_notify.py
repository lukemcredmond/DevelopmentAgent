"""Outbound-only phone notifications (Discord webhook). Never opens inbound ports."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

DEDUP_WINDOW_SEC = 120.0
BOARD_STATUS_DEDUP_WINDOW_SEC = 300.0
_DISCORD_HOSTS = frozenset(
    {
        "discord.com",
        "www.discord.com",
        "discordapp.com",
        "www.discordapp.com",
        "canary.discord.com",
        "ptb.discord.com",
    }
)

_dedup_lock = threading.Lock()
_recent: Dict[Tuple[str, str], float] = {}

# Injected in tests: (url, payload_bytes, timeout) -> None; raises on failure
_http_post: Optional[Callable[[str, bytes, float], None]] = None


def is_allowed_discord_webhook_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in _DISCORD_HOSTS:
        return False
    path = parsed.path or ""
    return "/api/webhooks/" in path


def _should_skip_dedup(kind: str, task_id: Optional[str]) -> bool:
    key = (kind, task_id or "")
    window = BOARD_STATUS_DEDUP_WINDOW_SEC if kind == "board_status" else DEDUP_WINDOW_SEC
    now = time.monotonic()
    with _dedup_lock:
        # prune
        stale = [k for k, t in _recent.items() if now - t > max(DEDUP_WINDOW_SEC, BOARD_STATUS_DEDUP_WINDOW_SEC)]
        for k in stale:
            _recent.pop(k, None)
        last = _recent.get(key)
        if last is not None and now - last < window:
            return True
        _recent[key] = now
        return False


def clear_notify_dedup() -> None:
    with _dedup_lock:
        _recent.clear()


def _post_discord(url: str, content: str, *, timeout: float = 8.0) -> None:
    body = json.dumps({"content": content[:1900]}).encode("utf-8")
    if _http_post is not None:
        _http_post(url, body, timeout)
        return
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "AllHands-PhoneNotify/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _send_sync(kind: str, title: str, body: str, *, task_id: Optional[str] = None) -> Dict[str, Any]:
    ws = get_workflow_settings()
    if not ws.get("phoneNotifyEnabled"):
        return {"ok": False, "skipped": "disabled"}
    if str(ws.get("phoneNotifyProvider") or "discord").lower() != "discord":
        return {"ok": False, "skipped": "unsupported_provider"}

    url = str(ws.get("phoneNotifyDiscordWebhookUrl") or "").strip()
    try:
        from backend.services.api_auth import resolve_discord_webhook_url

        url = resolve_discord_webhook_url(url)
    except Exception:
        pass
    if not url:
        return {"ok": False, "skipped": "missing_webhook"}
    if not is_allowed_discord_webhook_url(url):
        add_system_log(
            "System",
            "warning",
            "Phone notify: webhook URL rejected (must be https Discord /api/webhooks/…)",
        )
        return {"ok": False, "error": "invalid_webhook_url"}

    if _should_skip_dedup(kind, task_id):
        return {"ok": True, "skipped": "dedup"}

    text = f"**{title}**\n{body}".strip()
    try:
        _post_discord(url, text)
        add_system_log("System", "info", f"Phone notify sent ({kind})")
        return {"ok": True}
    except urllib.error.HTTPError as exc:
        add_system_log("System", "warning", f"Phone notify failed ({kind}): HTTP {exc.code}")
        return {"ok": False, "error": f"http_{exc.code}"}
    except Exception as exc:
        add_system_log("System", "warning", f"Phone notify failed ({kind}): {type(exc).__name__}")
        return {"ok": False, "error": type(exc).__name__}


def notify_event(
    kind: str,
    title: str,
    body: str,
    *,
    task_id: Optional[str] = None,
    sync: bool = False,
) -> None:
    """Fire-and-forget Discord notify (unless sync=True for tests / test endpoint)."""
    if sync:
        _send_sync(kind, title, body, task_id=task_id)
        return

    def _run() -> None:
        try:
            _send_sync(kind, title, body, task_id=task_id)
        except Exception:
            pass

    threading.Thread(target=_run, name=f"phone-notify-{kind}", daemon=True).start()


def notify_if_enabled(kind: str, title: str, body: str, *, task_id: Optional[str] = None) -> None:
    """Respect per-event workflow toggles, then notify_event."""
    ws = get_workflow_settings()
    if not ws.get("phoneNotifyEnabled"):
        return
    flag_defaults = {
        "needs_user": ("phoneNotifyOnNeedsUser", True),
        "needs_po": ("phoneNotifyOnNeedsPo", False),
        "tool_approval": ("phoneNotifyOnToolApproval", True),
        "sprint_end": ("phoneNotifyOnSprintEnd", True),
        "board_status": ("phoneNotifyOnBoardStatus", True),
        "stuck_escalation": ("phoneNotifyOnStuckEscalation", True),
        "step_timeout": ("phoneNotifyOnStepTimeout", True),
        "backup_armed": ("phoneNotifyOnBackupArmed", True),
        "test": (None, True),
    }
    entry = flag_defaults.get(kind)
    if entry is None:
        return
    flag, default_on = entry
    if flag is not None and not ws.get(flag, default_on):
        return
    notify_event(kind, title, body, task_id=task_id)


def send_test_notification(*, webhook_url_override: Optional[str] = None) -> Dict[str, Any]:
    """Synchronous test send for API / UI."""
    clear_notify_dedup()
    if webhook_url_override and webhook_url_override.strip():
        # Temporary override for test without requiring a prior save race
        ws = get_workflow_settings()
        if not ws.get("phoneNotifyEnabled"):
            return {"ok": False, "skipped": "disabled"}
        url = webhook_url_override.strip()
        if not is_allowed_discord_webhook_url(url):
            return {"ok": False, "error": "invalid_webhook_url"}
        text = (
            "**AllHands test**\n"
            "Outbound phone notify is working. This PC did not open any inbound ports."
        )
        try:
            _post_discord(url, text)
            add_system_log("System", "info", "Phone notify sent (test)")
            return {"ok": True}
        except urllib.error.HTTPError as exc:
            add_system_log("System", "warning", f"Phone notify failed (test): HTTP {exc.code}")
            return {"ok": False, "error": f"http_{exc.code}"}
        except Exception as exc:
            add_system_log("System", "warning", f"Phone notify failed (test): {type(exc).__name__}")
            return {"ok": False, "error": type(exc).__name__}
    return _send_sync(
        "test",
        "AllHands test",
        "Outbound phone notify is working. This PC did not open any inbound ports.",
        task_id="test",
    )
