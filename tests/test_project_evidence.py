"""Project evidence inject + recommended lint command on state."""

from unittest.mock import patch

from backend import state
from backend.bootstrap import initialize
from backend.services.project_evidence import (
    clear_project_evidence,
    format_project_evidence_for_prompt,
    inject_project_tool_evidence,
    load_project_evidence,
)


def test_inject_project_evidence_persists_and_formats_prompt():
    initialize()
    clear_project_evidence()
    entry = inject_project_tool_evidence(
        "run_command",
        {"command": "npm run lint"},
        "error • src/a.ts:1:1 — unused",
        note="from CI",
    )
    assert entry["id"]
    assert entry["command"] == "npm run lint"
    assert len(state.PROJECT_TOOL_EVIDENCE) == 1
    assert any(e.get("source") == "user" and e.get("taskId") in (None, "") for e in state.TOOL_EXECUTION_LOG[-3:])

    block = format_project_evidence_for_prompt()
    assert "PROJECT SHARED EVIDENCE" in block
    assert "npm run lint" in block
    assert "unused" in block

    # reload from settings
    state.PROJECT_TOOL_EVIDENCE = []
    load_project_evidence()
    assert len(state.PROJECT_TOOL_EVIDENCE) == 1


def test_recommended_lint_command_in_state_response(tmp_path, monkeypatch):
    initialize()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "package.json").write_text('{"scripts":{"lint":"eslint ."}}', encoding="utf-8")
    from backend.api.helpers import build_state_response

    with patch("backend.workspace.files.sync_virtual_filesystem_from_disk", return_value={}):
        resp = build_state_response(include_files=False)
    assert resp.get("recommendedLintCommand") == "npm run lint"
    assert "projectToolEvidence" in resp


def test_build_task_prompt_includes_project_evidence():
    initialize()
    clear_project_evidence()
    inject_project_tool_evidence(
        "run_command",
        {"command": "dotnet build"},
        "Build succeeded.",
    )
    from backend.agents.task_context import build_task_prompt, init_new_task

    task = init_new_task({"id": "T-EV", "title": "X", "description": "Y", "status": "In Progress"})
    prompt = build_task_prompt(task, "brief")
    assert "PROJECT SHARED EVIDENCE" in prompt
    assert "dotnet build" in prompt
