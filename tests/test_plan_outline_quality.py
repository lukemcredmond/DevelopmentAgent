"""Plan outline rubric and normalize helper."""

from backend.bootstrap import initialize
from backend.services.plan_outline_quality import (
    normalize_pasted_plan,
    sample_v2_plan_outline,
    validate_plan_outline,
)


def test_rejects_instruction_echo_json():
    payload = (
        '{"description":"Produce a concise markdown project plan ONLY for the app, '
        'including Summary, Approach, Risks, Open questions, and product epics.",'
        '"acceptanceCriteria":["Plan is markdown","Plan contains epics","Plan includes Summary"]}'
    )
    v = validate_plan_outline(payload, brief="x" * 500, strict=True)
    assert not v.ok
    assert any(i.code == "json_not_markdown" for i in v.issues)


def test_accepts_minimal_v2_outline():
    outline = sample_v2_plan_outline()
    v = validate_plan_outline(outline, brief="Build flutter meal planner offline", strict=True)
    assert v.ok
    assert v.epic_bullet_count >= 6
    assert v.epic_detail_count >= 6


def test_normalize_pasted_plan_strips_preamble():
    raw = "Let me think...\n\n# Plan: Meal app\n\n## Summary\nHi.\n"
    out = normalize_pasted_plan(raw)
    assert out.startswith("## Summary") or out.startswith("# Plan")


def test_user_edit_non_strict_allows_thin_outline():
    initialize()
    thin = "## Summary\nx\n\n## Approach\ny\n\n## Proposed epics\n- A — one\n- B — two\n"
    v = validate_plan_outline(thin, brief="tiny", strict=False)
    assert v.ok
