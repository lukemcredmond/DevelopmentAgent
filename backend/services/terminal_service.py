import subprocess
from typing import Any, Dict, List

from backend import state
from backend.config import TERMINAL_TIMEOUT_SEC
from backend.services.command_policy import split_chained_commands, validate_command


def _run_single(command: str, timeout: int) -> Dict[str, Any]:
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


def run_command(command: str, timeout: int = TERMINAL_TIMEOUT_SEC) -> Dict[str, Any]:
    """Runs shell command(s) sandboxed to the workspace directory."""
    ok, reason = validate_command(command)
    if not ok:
        return {
            "success": False,
            "stdout": "",
            "stderr": reason,
            "returncode": -1,
        }

    segments = split_chained_commands(command)
    if len(segments) == 1:
        return _run_single(segments[0], timeout)

    combined_out: List[str] = []
    combined_err: List[str] = []
    last_code = 0
    for i, seg in enumerate(segments, start=1):
        result = _run_single(seg, timeout)
        combined_out.append(f"--- [{i}/{len(segments)}] {seg} ---\n{result.get('stdout') or ''}")
        if result.get("stderr"):
            combined_err.append(str(result["stderr"]))
        last_code = int(result.get("returncode") or 0)
        if last_code != 0:
            break
    return {
        "success": last_code == 0,
        "stdout": "\n".join(combined_out),
        "stderr": "\n".join(combined_err),
        "returncode": last_code,
    }
