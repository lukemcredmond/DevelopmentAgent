import subprocess
from typing import Any, Dict, List

from backend import state
from backend.config import TERMINAL_TIMEOUT_SEC


def run_command(command: str, timeout: int = TERMINAL_TIMEOUT_SEC) -> Dict[str, Any]:
    """Runs a shell command sandboxed to the workspace directory."""
    blocked = ["&&", "||", ";", "|", ">", "<", "`", "$(", "${"]
    lower = command.lower()
    if any(token in command for token in blocked):
        return {
            "success": False,
            "stdout": "",
            "stderr": "Chained or redirected commands are not allowed.",
            "returncode": -1,
        }
    if "cd " in lower or lower.strip().startswith("cd"):
        return {
            "success": False,
            "stdout": "",
            "stderr": "Directory changes are not allowed; commands run in workspace root.",
            "returncode": -1,
        }

    try:
        if state.WORKSPACE_DIR:
            import os

            os.makedirs(state.WORKSPACE_DIR, exist_ok=True)

        result = subprocess.run(
            command,
            shell=True,
            cwd=state.WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }
