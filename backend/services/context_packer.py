"""Optional Repomix / code2prompt CLI wrappers for codebase_pack prompt section.

Install: see README § "Installing Repomix or code2prompt".
Extension point: add modes (e.g. caveman) with the same run_context_pack contract.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from backend import state
from backend.services.workflow_settings import get_workflow_settings

logger = logging.getLogger(__name__)


def run_context_pack(
    paths: List[str],
    *,
    mode: Optional[str] = None,
    query_hint: str = "",
) -> str:
    """Run configured packer CLI in workspace; return stdout text or empty on failure."""
    ws = get_workflow_settings()
    pack_mode = (mode or ws.get("contextPacker") or "off").strip().lower()
    if pack_mode in ("", "off", "none", "false"):
        return ""

    workspace = Path(state.WORKSPACE_DIR or "./workspace").resolve()
    if not workspace.is_dir():
        return ""

    max_chars = int(ws.get("contextPackerMaxChars") or 12000)
    timeout = int(ws.get("terminalTimeoutSec") or 600)

    globs = [p for p in paths if p]
    if not globs:
        globs = ["."]

    try:
        if pack_mode == "repomix":
            cmd = _build_repomix_command(ws, workspace, globs)
        elif pack_mode == "code2prompt":
            cmd = _build_code2prompt_command(ws, workspace, globs, query_hint)
        else:
            logger.info("context_packer: unknown mode %s", pack_mode)
            return ""
        if not cmd:
            return ""
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=max(30, timeout),
            shell=False,
        )
        if result.returncode != 0:
            logger.warning(
                "context_packer %s failed (code %s): %s",
                pack_mode,
                result.returncode,
                (result.stderr or result.stdout or "")[:500],
            )
            return ""
        out = (result.stdout or "").strip()
        if len(out) > max_chars:
            out = out[: max_chars - 40] + "\n...[packer output truncated]\n"
        return out
    except subprocess.TimeoutExpired:
        logger.warning("context_packer %s timed out", pack_mode)
        return ""
    except FileNotFoundError:
        logger.warning("context_packer %s command not found on PATH", pack_mode)
        return ""
    except Exception as exc:
        logger.warning("context_packer error: %s", exc)
        return ""


def _build_repomix_command(ws: dict, workspace: Path, globs: List[str]) -> Optional[List[str]]:
    exe = str(ws.get("repomixCommand") or "repomix").strip() or "repomix"
    include = ",".join(globs[:20])
    return [exe, "--stdout", "--include", include]


def _build_code2prompt_command(
    ws: dict,
    workspace: Path,
    globs: List[str],
    query_hint: str,
) -> Optional[List[str]]:
    exe = str(ws.get("code2promptCommand") or "code2prompt").strip() or "code2prompt"
    args = [exe, str(workspace)]
    if query_hint.strip():
        args.extend(["--filter", query_hint.strip()[:200]])
    for g in globs[:10]:
        args.extend(["--include", g])
    return args
