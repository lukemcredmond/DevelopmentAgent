from fastapi import APIRouter

from backend.services.git_service import git_init, git_status, parse_git_status

router = APIRouter()


@router.get("/api/git/status")
def get_git_status():
    # Do not hold STATE_LOCK across git subprocess calls.
    result = git_status()
    if not result.get("success") and "not a git repository" in result.get("stderr", "").lower():
        git_init()
        result = git_status()
    parsed = parse_git_status(result.get("stdout", ""))
    return {
        **parsed,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "success": result.get("success", False),
    }
