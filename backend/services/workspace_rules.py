"""Load project rules from workspace (AGENTS.md, .cursor/rules, .allhands/rules)."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from backend import state

_RULE_FILENAMES = (
    "AGENTS.md",
    "agents.md",
    ".allhands/rules.md",
    ".allhands/RULES.md",
)
_RULE_DIRS = (
    ".cursor/rules",
    ".allhands/rules",
)


def _workspace_root() -> str:
    return str(getattr(state, "WORKSPACE_DIR", "") or "").strip()


def _read_bounded(path: str, max_chars: int, used: int) -> Tuple[str, int]:
    if used >= max_chars or not os.path.isfile(path):
        return "", used
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return "", used
    rel = os.path.relpath(path, _workspace_root()) if _workspace_root() else path
    block = f"\n=== WORKSPACE RULE: {rel} ===\n{text.strip()}\n"
    if len(block) + used > max_chars:
        remaining = max(200, max_chars - used - 40)
        block = block[:remaining] + "\n...[rules truncated]\n"
    return block, used + len(block)


def collect_workspace_rules_paths() -> List[str]:
    root = _workspace_root()
    if not root or not os.path.isdir(root):
        return []
    found: List[str] = []
    for name in _RULE_FILENAMES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            found.append(path)
    for rel_dir in _RULE_DIRS:
        dir_path = os.path.join(root, rel_dir)
        if not os.path.isdir(dir_path):
            continue
        for dir_root, _dirs, files in os.walk(dir_path):
            for fname in sorted(files):
                if not fname.lower().endswith((".md", ".mdc", ".txt")):
                    continue
                found.append(os.path.join(dir_root, fname))
    return found


def load_workspace_rules_context(*, max_chars: Optional[int] = None) -> str:
    ws = None
    try:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    except Exception:
        pass
    if ws is not None and not ws.get("enableWorkspaceRulesInject", True):
        return ""
    cap = int(max_chars or (ws or {}).get("workspaceRulesMaxChars") or 12000)
    if cap <= 0:
        return ""
    paths = collect_workspace_rules_paths()
    if not paths:
        return ""
    parts: List[str] = ["\n=== PROJECT WORKSPACE RULES ==="]
    used = len(parts[0])
    for path in paths:
        block, used = _read_bounded(path, cap, used)
        if block:
            parts.append(block)
        if used >= cap:
            break
    if len(parts) <= 1:
        return ""
    return "".join(parts)
