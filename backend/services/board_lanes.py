"""Kanban lane normalization — migrate legacy boards to the PO → Dev → QA workflow."""

from typing import Any, Dict

from backend.services.workflow_settings import get_workflow_settings


BASE_LANES = ["Backlog", "In Progress", "Needs PO", "Needs User", "QA", "Done"]


def normalize_board_lanes(board: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures workflow lanes exist and migrates legacy 'Code Review' tasks to QA
    when code review gate is off.
    """
    if "Needs User" not in board:
        board["Needs User"] = []
    if "Needs PO" not in board:
        board["Needs PO"] = []

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
