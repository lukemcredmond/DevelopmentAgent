from fastapi import APIRouter

from backend import state
from backend.services.git_service import git_init, git_status

router = APIRouter()


@router.get("/api/git/status")
def get_git_status():
    with state.STATE_LOCK:
        result = git_status()
        if not result.get("success") and "not a git repository" in result.get("stderr", "").lower():
            git_init()
            result = git_status()
        return {
            "branch": _parse_branch(result.get("stdout", "")),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "success": result.get("success", False),
        }


def _parse_branch(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("##"):
            part = line[2:].strip().split("...")[0]
            return part
    return "main"
