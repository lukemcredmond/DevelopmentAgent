"""Kanban lane normalization — migrate legacy boards to the PO → Dev → QA workflow."""

from typing import Any, Dict, Optional

from backend.services.workflow_settings import get_workflow_settings


FEATURES_LANE = "Features"

BASE_LANES = [
    FEATURES_LANE,
    "Backlog",
    "Refinement",
    "Blocked",
    "In Progress",
    "Needs PO",
    "Needs User",
    "QA",
    "Done",
]

_LANE_ALIASES = {
    "in progress": "In Progress",
    "needs po": "Needs PO",
    "needs user": "Needs User",
    "code review": "Code Review",
    "done": "Done",
    "qa": "QA",
    "backlog": "Backlog",
    "refinement": "Refinement",
    "blocked": "Blocked",
    "features": FEATURES_LANE,
}


def canonical_lane(name: str, *, board: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return canonical lane name or None if unknown."""
    raw = str(name or "").strip()
    if not raw:
        return None
    lanes = list(BASE_LANES)
    if board:
        lanes = list(dict.fromkeys(list(board.keys()) + lanes))
    for lane in lanes:
        if lane.lower() == raw.lower():
            return lane
    return _LANE_ALIASES.get(raw.lower())


def normalize_board_lanes(board: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures workflow lanes exist and migrates legacy 'Code Review' tasks to QA
    when code review gate is off.
    """
    if "Needs User" not in board:
        board["Needs User"] = []
    if "Needs PO" not in board:
        board["Needs PO"] = []
    if "Blocked" not in board:
        board["Blocked"] = []

    settings = get_workflow_settings()
    if settings.get("requireBacklogApproval"):
        board.setdefault("Pending Approval", [])
    if settings.get("requireCodeReview"):
        board.setdefault("Code Review", [])
    else:
        legacy_review = board.pop("Code Review", [])
        if legacy_review:
            qa_lane = board.setdefault("QA", [])
            for task in legacy_review:
                task["status"] = "QA"
                qa_lane.append(task)

    for lane in BASE_LANES:
        board.setdefault(lane, [])

    return board
