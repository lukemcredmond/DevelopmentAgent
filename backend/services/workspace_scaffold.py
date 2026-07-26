"""Allowlisted auto-scaffold when workspace structure is critically incomplete."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from backend import state
from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings
from backend.services.workspace_structure_audit import (
    audit_workspace_structure,
    format_structure_audit,
    workspace_looks_empty_for_stack,
)
from backend.workspace.files import sync_virtual_filesystem_from_disk


def _safe_project_slug(name: Optional[str] = None) -> str:
    raw = (name or getattr(state, "PROJECT_NAME", None) or "App").strip()
    slug = re.sub(r"[^A-Za-z0-9_]+", "", raw.replace(" ", ""))
    if not slug or not slug[0].isalpha():
        slug = "App" + slug
    return slug[:40] or "App"


def _write_text(rel_path: str, content: str) -> None:
    ws = state.WORKSPACE_DIR
    abs_path = os.path.join(ws, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_path) or ws, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    sync_virtual_filesystem_from_disk()


def _run_allowlisted(command: str) -> Dict[str, Any]:
    from backend.services.command_result import run_workspace_command

    result = run_workspace_command(command)
    return {
        "ok": result.outcome == "ok" or result.exit_code == 0,
        "exit_code": result.exit_code,
        "summary": result.summary or result.outcome,
        "output": (result.combined_output or "")[:1500],
    }


def scaffold_python_stubs() -> Dict[str, Any]:
    ws = state.WORKSPACE_DIR
    created: list[str] = []
    if not os.path.isfile(os.path.join(ws, "pyproject.toml")) and not os.path.isfile(
        os.path.join(ws, "requirements.txt")
    ):
        _write_text(
            "pyproject.toml",
            '[project]\nname = "app"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n',
        )
        created.append("pyproject.toml")
    if not os.path.isdir(os.path.join(ws, "src")):
        _write_text("src/__init__.py", '"""Application package."""\n')
        created.append("src/__init__.py")
    return {"ok": True, "method": "write_file", "created": created}


def scaffold_react_vite() -> Dict[str, Any]:
    ws = state.WORKSPACE_DIR
    if os.path.isdir(os.path.join(ws, "src")):
        return {"ok": False, "skipped": "src_exists"}
    # Non-interactive Vite scaffold into current directory
    cmd = "npm create vite@latest . -- --template react-ts"
    return {"method": "cli", "command": cmd, **_run_allowlisted(cmd)}


def scaffold_dotnet(task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    brief = (getattr(state, "PROJECT_BRIEF", None) or "").lower()
    title = str((task or {}).get("title") or "").lower()
    desc = str((task or {}).get("description") or "").lower()
    blob = f"{brief} {title} {desc}"
    template = "webapi"
    if any(w in blob for w in ("library", "classlib", "class lib", "nuget")):
        template = "classlib"
    elif any(w in blob for w in ("console", "cli tool")):
        template = "console"
    name = _safe_project_slug()
    cmd = f"dotnet new {template} -n {name} --force"
    return {"method": "cli", "command": cmd, "template": template, **_run_allowlisted(cmd)}


def maybe_auto_scaffold(
    task: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Run allowlisted scaffold once when structure is critically incomplete.
    Unity: guidance only (no CLI create).
    """
    ws_settings = get_workflow_settings()
    if not ws_settings.get("autoScaffoldOnStructureGap", True) and not force:
        return {"ok": False, "skipped": "disabled"}

    task_id = str((task or {}).get("id") or "")
    board_task = find_task_by_id(task_id) if task_id else None
    target = board_task or task or {}
    if target.get("structureScaffoldAttempted") and not force:
        return {"ok": False, "skipped": "already_attempted"}

    audit = audit_workspace_structure()
    if audit.get("stack") == "unity_quest":
        # No Unity Editor create — leave guidance in audit warnings only
        return {"ok": False, "skipped": "unity_no_cli", "audit": audit}

    if not audit.get("critical") or not workspace_looks_empty_for_stack(audit):
        return {"ok": False, "skipped": "not_eligible", "audit": audit}

    if board_task is not None:
        board_task["structureScaffoldAttempted"] = True
    elif task is not None:
        task["structureScaffoldAttempted"] = True

    stack = audit.get("stack")
    add_system_log(
        "Developer",
        "info",
        f"Auto-scaffold: attempting for stack={stack} (critical structure gap)",
    )

    if stack in ("react_vite", "react_next"):
        # Next gaps: still use Vite only when no app/ yet; if next-only missing page, skip CLI
        if stack == "react_next" and os.path.isdir(os.path.join(state.WORKSPACE_DIR, "app")):
            result = {"ok": False, "skipped": "next_partial"}
        else:
            result = scaffold_react_vite()
    elif stack == "dotnet":
        result = scaffold_dotnet(board_task or task)
    elif stack == "python":
        result = scaffold_python_stubs()
    else:
        result = {"ok": False, "skipped": f"unsupported_stack:{stack}"}

    sync_virtual_filesystem_from_disk()
    after = audit_workspace_structure()
    result["audit_before"] = audit
    result["audit_after"] = after
    result["structure_ok_after"] = not bool(after.get("critical"))

    if task_id:
        record_task_decision(
            task_id,
            "System",
            "structure_scaffold",
            f"Auto-scaffold {stack}: ok={result.get('ok')} skipped={result.get('skipped')}",
            detail=format_structure_audit(after)[:500],
        )
    add_system_log(
        "Developer",
        "info" if result.get("ok") or result.get("structure_ok_after") else "warning",
        f"Auto-scaffold finished stack={stack} ok={result.get('ok')} "
        f"structure_ok={result.get('structure_ok_after')}",
    )
    return result
