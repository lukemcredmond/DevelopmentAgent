"""Auto Blocked lane + agent step duration timeout."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task, get_task_lane
from backend.bootstrap import initialize
from backend.services.blocked_lane import sync_blocked_lane
from backend.services.board_service import move_board_stage
from backend.services.sprint_service import _try_backlog_handler, has_sprint_work
from backend.services.workflow_settings import get_active_lanes


def _empty_board():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "Blocked",
        "In Progress",
        "Needs PO",
        "Needs User",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_enter_blocked_when_deps_unmet():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ):
        dep = init_new_task(
            {
                "id": "DEP-1",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-1",
                "title": "Parent",
                "description": "p",
                "status": "Backlog",
                "blockedBy": ["DEP-1"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, dep]
        result = sync_blocked_lane(persist=False)
        assert result["entered"] == 1
        assert get_task_lane("PAR-1") == "Blocked"
        assert parent.get("blockedReturnLane") == "Backlog"
        assert get_task_lane("DEP-1") == "Backlog"


def test_release_blocked_when_dep_done():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ), patch(
        "backend.services.blocked_lane.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ):
        dep = init_new_task(
            {
                "id": "DEP-2",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-2",
                "title": "Parent",
                "description": "p",
                "status": "Backlog",
                "blockedBy": ["DEP-2"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, dep]
        sync_blocked_lane(persist=False)
        assert get_task_lane("PAR-2") == "Blocked"

        move_board_stage("DEP-2", "Done")
        assert get_task_lane("PAR-2") == "Backlog"
        assert "blockedReturnLane" not in parent or not parent.get("blockedReturnLane")


def test_on_task_completed_releases_blocked_waiters():
    """Completion hook alone (not only move_board_stage post-sync) frees Blocked cards."""
    from backend.agents.task_context import on_task_completed

    _empty_board()
    settings = {"enableBlockedLane": True, "requireBacklogRefinement": False}
    with patch(
        "backend.services.blocked_lane.get_workflow_settings",
        return_value=settings,
    ), patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value=settings,
    ):
        dep = init_new_task(
            {
                "id": "DEP-HOOK",
                "title": "Dep",
                "description": "d",
                "status": "Done",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-HOOK",
                "title": "Parent",
                "description": "p",
                "status": "Blocked",
                "blockedBy": ["DEP-HOOK"],
                "requiresDev": True,
                "blockedReturnLane": "Backlog",
            }
        )
        state.SHARED_BOARD["Done"] = [dep]
        state.SHARED_BOARD["Blocked"] = [parent]
        on_task_completed("DEP-HOOK")
        assert get_task_lane("PAR-HOOK") == "Backlog"


def test_release_to_refinement_when_refinement_required():
    _empty_board()
    settings = {
        "enableBlockedLane": True,
        "requireBacklogRefinement": True,
    }
    with patch(
        "backend.services.blocked_lane.get_workflow_settings",
        return_value=settings,
    ), patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value=settings,
    ):
        dep = init_new_task(
            {
                "id": "DEP-REF",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-REF",
                "title": "Parent",
                "description": "p",
                "status": "Refinement",
                "blockedBy": ["DEP-REF"],
                "requiresDev": True,
                "refinementComplete": False,
            }
        )
        state.SHARED_BOARD["Refinement"] = [parent]
        state.SHARED_BOARD["Backlog"] = [dep]
        sync_blocked_lane(persist=False)
        assert get_task_lane("PAR-REF") == "Blocked"
        assert parent.get("blockedReturnLane") == "Refinement"

        move_board_stage("DEP-REF", "Done")
        assert get_task_lane("PAR-REF") == "Refinement"


def test_in_progress_not_auto_yanked_to_blocked():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ):
        dep = init_new_task(
            {
                "id": "DEP-3",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        active = init_new_task(
            {
                "id": "ACT-1",
                "title": "Active",
                "description": "a",
                "status": "In Progress",
                "blockedBy": ["DEP-3"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [dep]
        state.SHARED_BOARD["In Progress"] = [active]
        result = sync_blocked_lane(persist=False)
        assert result["entered"] == 0
        assert get_task_lane("ACT-1") == "In Progress"


def test_sprint_does_not_claim_blocked_cards():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={
            "enableBlockedLane": True,
            "requireBacklogRefinement": False,
            "prioritizeImplementationOverRefinement": True,
            "requireCodeReview": False,
        },
    ):
        parent = init_new_task(
            {
                "id": "PAR-BLK",
                "title": "Parent only",
                "description": "waiting",
                "status": "Blocked",
                "blockedBy": ["MISSING"],
                "requiresDev": True,
                "blockedReturnLane": "Backlog",
            }
        )
        state.SHARED_BOARD["Blocked"] = [parent]
        handler, task = _try_backlog_handler()
        assert handler is None
        assert task is None
        assert get_task_lane("PAR-BLK") in ("Blocked", "Needs User")


def test_enable_blocked_lane_false_skips_moves():
    _empty_board()
    settings = {"enableBlockedLane": False, "requireBacklogRefinement": False}
    with patch(
        "backend.services.blocked_lane.get_workflow_settings",
        return_value=settings,
    ), patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value=settings,
    ):
        dep = init_new_task(
            {
                "id": "DEP-OFF",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-OFF",
                "title": "Parent",
                "description": "p",
                "status": "Backlog",
                "blockedBy": ["DEP-OFF"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, dep]
        result = sync_blocked_lane(persist=False)
        assert result == {"entered": 0, "released": 0}
        assert get_task_lane("PAR-OFF") == "Backlog"
        lanes = get_active_lanes({"enableBlockedLane": False})
        assert "Blocked" not in lanes


def test_has_sprint_work_with_blocked_parent_and_claimable_dep():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={
            "enableBlockedLane": True,
            "requireBacklogRefinement": False,
            "requireCodeReview": False,
        },
    ):
        dep = init_new_task(
            {
                "id": "DEP-HSW",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "PAR-HSW",
                "title": "Parent",
                "description": "p",
                "status": "Blocked",
                "blockedBy": ["DEP-HSW"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Blocked"] = [parent]
        state.SHARED_BOARD["Backlog"] = [dep]
        assert has_sprint_work() is True


def test_agent_step_timeout_returns_timed_out_message():
    from backend.agents.registry import agent_dev

    initialize()
    _empty_board()
    state.ACTIVE_SPRINT_TASK_ID = None
    clock = {"n": 0}

    def mono_seq():
        clock["n"] += 1
        # First call = step start; any later call is past the 30s cap.
        if clock["n"] == 1:
            return 0.0
        return 100.0

    with patch(
        "backend.agents.scrum_agent.get_workflow_settings",
        return_value={
            "maxToolFailuresPerStep": 5,
            "maxAgentStepDurationSec": 30,
            "ollamaNumCtx": 2048,
            "ollamaKeepAlive": "30m",
            "maxInCardLintFixes": 5,
        },
    ), patch(
        "backend.agents.scrum_agent.time.monotonic",
        side_effect=mono_seq,
    ), patch.object(
        agent_dev, "_chat", side_effect=AssertionError("LLM should not be called after timeout")
    ), patch(
        "backend.agents.registry.configure_agent_tools"
    ), patch(
        "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
    ), patch.object(
        agent_dev, "_build_system_content", return_value="sys"
    ), patch.object(
        agent_dev, "_build_user_content", return_value="user"
    ):
        result = agent_dev.execute_step("do work", max_iterations=8)

    assert result.startswith("Timed out:")
    assert "unbounded loop" in result
    assert "min" in result


def test_readme_vocabulary_markers():
    from pathlib import Path

    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "Blocked (lane)" in text
    assert "Agent loop stop" in text
    assert "maxAgentStepDurationSec" in text
    assert "enableBlockedLane" in text


def test_chat_stop_cancelled_marker():
    from pathlib import Path

    chat = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "components"
        / "ChatPanel.tsx"
    )
    text = chat.read_text(encoding="utf-8")
    assert "Stopped — request cancelled" in text
