"""Phase 3 cursor parity: cap exempt, latch dedupe, backup guard."""

from backend import state
from backend.services.backup_model import dev_backup_model_allowed
from backend.services.sprint_service import (
    _latched_card_skip_auto_sprint,
    _needs_user_cap_reached,
)
from backend.services.lint_wall_recovery import _pubspec_add_dependency_patch
from backend.services.workflow_settings import save_workflow_settings


def test_needs_user_cap_exempt_stuck_loop_and_phase_cycle_cap():
    save_workflow_settings({"autonomousMode": True, "maxNeedsUserPerSprint": 1})
    state.SPRINT_NEEDS_USER_COUNT = 5
    assert _needs_user_cap_reached(kind="stuck_loop") is False
    assert _needs_user_cap_reached(kind="phase_cycle_cap") is False
    assert _needs_user_cap_reached(kind="clarification") is True


def test_latched_card_skip_after_failed_park_or_recovery():
    base = {"id": "T1", "phaseCycleCapReached": True}
    assert _latched_card_skip_auto_sprint(base) is False
    assert _latched_card_skip_auto_sprint({**base, "parkFailed": True}) is True
    assert _latched_card_skip_auto_sprint({**base, "poAutoSkip": True}) is True
    assert _latched_card_skip_auto_sprint({**base, "latchedRecoveryAttempted": True}) is True


def test_dev_backup_rejects_ornith_and_accepts_coder():
    ok, reason = dev_backup_model_allowed("ornith-1.5-35b")
    assert ok is False
    assert "known_refusal" in reason
    ok2, _ = dev_backup_model_allowed("qwen2.5-coder:14b")
    assert ok2 is True


def test_pubspec_add_dependency_patch_inserts_under_dependencies():
    content = "name: app\nenvironment:\n  sdk: '>=3.0.0'\ndependencies:\n  flutter:\n"
    det = _pubspec_add_dependency_patch(content, "drift")
    assert det is not None
    _, old, new = det
    assert old == "dependencies:"
    assert "drift:" in new
    already = content.replace("dependencies:", "dependencies:\n  drift: ^2.0.0", 1)
    assert _pubspec_add_dependency_patch(already, "drift") is None


def test_build_info_includes_phase3_and_phase4_recovery_features():
    from backend.services.build_info import RECOVERY_FEATURES

    for flag in (
        "phase3_latch_dedupe",
        "phase4_composer_dev_step",
        "phase4_text_rejection_park",
    ):
        assert flag in RECOVERY_FEATURES


def test_has_sprint_work_false_when_only_poautoskip_latched_card():
    from backend.bootstrap import initialize
    from backend.services.sprint_service import has_sprint_work

    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs PO", "Needs User", "QA", "Done"):
        state.SHARED_BOARD[lane] = []
    state.SHARED_BOARD["In Progress"] = [
        {
            "id": "T-LATCH",
            "title": "Stuck",
            "status": "In Progress",
            "phaseCycleCapReached": True,
            "latchedRecoveryAttempted": True,
            "poAutoSkip": True,
            "parkFailed": True,
        }
    ]
    assert has_sprint_work() is False


def test_llm_iterations_capped_for_implementer():
    from backend.services.sprint_service import _llm_iterations

    save_workflow_settings(
        {
            "executionProfile": "implementer",
            "maxLlmIterationsPerStep": 30,
            "implementerMaxLlmIterationsPerStep": 5,
        }
    )
    assert _llm_iterations({}) == 5
