"""Needs User escalation when text rejection loops on lint/tool cards."""

from backend.services.needs_user_guard import (
    build_needs_user_brief,
    should_escalate_to_needs_user,
    should_park_in_needs_user,
)


def test_text_rejection_escalation_allowed_with_lint_diagnostics():
    task = {
        "id": "T-TEXT",
        "title": "Define Tables",
        "consecutiveBadExits": 3,
        "lastCircuitExitReason": "text_rejection_loop",
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
        "lastCommandDiagnostics": [
            {"file": "lib/database.dart", "line": 1, "message": "Undefined class"},
        ],
    }
    msg = "Model returned text-only (2×) instead of tools on 'Define Tables'."
    allowed, reason = should_escalate_to_needs_user(task, msg, kind="stuck_loop")
    assert allowed is True
    assert reason == ""
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg=msg)
    park_ok, park_reason = should_park_in_needs_user(task, brief)
    assert park_ok is True
    assert park_reason == ""
