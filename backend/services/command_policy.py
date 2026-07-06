"""Command auto-run policy (Cursor-style allowlist/denylist) and safe chaining."""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Tuple

from backend.services.workflow_settings import get_workflow_settings

BLOCKED_ALWAYS = ("|", ">", "<", "`", "$(", "${")
CHAIN_SPLITTERS = ("&&", ";")


def _normalize_command(command: str) -> str:
    return " ".join(str(command or "").strip().split())


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command, posix=True)
        return parts[0].lower() if parts else ""
    except ValueError:
        return command.strip().split()[0].lower() if command.strip() else ""


def _matches_patterns(command: str, patterns: List[str]) -> bool:
    cmd = _normalize_command(command).lower()
    token = _first_token(command)
    for raw in patterns or []:
        pat = str(raw or "").strip().lower()
        if not pat:
            continue
        if pat.endswith("*"):
            if cmd.startswith(pat[:-1]) or token.startswith(pat[:-1]):
                return True
        elif pat in cmd or pat == token:
            return True
    return False


def command_auto_run_mode() -> str:
    """off | allowlist | denylist | all"""
    ws = get_workflow_settings()
    return str(ws.get("commandAutoRunMode") or "off").lower()


def run_command_requires_approval(command: str) -> bool:
    """
    When requireToolApproval is on, decide if this specific run_command needs user approval.
    Write tools still use toolApprovalTools separately.
    """
    ws = get_workflow_settings()
    if not ws.get("requireToolApproval"):
        return False
    mode = command_auto_run_mode()
    cmd = _normalize_command(command)
    if not cmd:
        return True
    if mode == "all":
        return False
    if mode == "allowlist":
        allow = ws.get("commandAllowlist") or []
        return not _matches_patterns(cmd, allow)
    if mode == "denylist":
        deny = ws.get("commandDenylist") or []
        return _matches_patterns(cmd, deny)
    return True


def validate_command(command: str) -> Tuple[bool, str]:
    """Validate command string before execution."""
    cmd = str(command or "").strip()
    if not cmd:
        return False, "Empty command."

    ws = get_workflow_settings()
    allow_chain = bool(ws.get("allowChainedCommands"))

    if allow_chain:
        for token in BLOCKED_ALWAYS:
            if token in cmd:
                return False, f"Blocked shell operator '{token}' is not allowed."
        lower = cmd.lower()
        if "cd " in lower or lower.strip().startswith("cd"):
            return False, "Directory changes are not allowed; commands run in workspace root."
        return True, ""

    for token in BLOCKED_ALWAYS + ("&&", "||", ";"):
        if token in cmd:
            return False, f"Chained or redirected commands are not allowed (enable allowChainedCommands for && and ;)."
    lower = cmd.lower()
    if "cd " in lower or lower.strip().startswith("cd"):
        return False, "Directory changes are not allowed; commands run in workspace root."
    return True, ""


def split_chained_commands(command: str) -> List[str]:
    """Split on && and ; when chaining enabled."""
    ws = get_workflow_settings()
    if not ws.get("allowChainedCommands"):
        return [command.strip()]
    parts: List[str] = []
    remaining = command.strip()
    while remaining:
        split_at = None
        split_len = 0
        for sep in CHAIN_SPLITTERS:
            idx = remaining.find(sep)
            if idx >= 0 and (split_at is None or idx < split_at):
                split_at = idx
                split_len = len(sep)
        if split_at is None:
            part = remaining.strip()
            if part:
                parts.append(part)
            break
        head = remaining[:split_at].strip()
        if head:
            parts.append(head)
        remaining = remaining[split_at + split_len :].strip()
    return parts or [command.strip()]
