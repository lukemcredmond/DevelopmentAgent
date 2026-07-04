import subprocess
from typing import Any, Dict, Optional

from backend import state


def _run_git(args: list[str], timeout: int = 30) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["git"] + args,
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
        return {"success": False, "stdout": "", "stderr": "Git command timed out", "returncode": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "git executable not found", "returncode": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def git_init() -> Dict[str, Any]:
    return _run_git(["init"])


def git_status() -> Dict[str, Any]:
    return _run_git(["status", "--short", "--branch"])


def git_diff(path: Optional[str] = None) -> Dict[str, Any]:
    args = ["diff"]
    if path:
        args.append(path)
    return _run_git(args)


def git_commit(message: str) -> Dict[str, Any]:
    add_result = _run_git(["add", "-A"])
    if not add_result["success"]:
        return add_result
    return _run_git(["commit", "-m", message])
