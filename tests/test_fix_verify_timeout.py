"""Fix-verify × timeout / cancel interaction + RAG retrieval smoke."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services.fix_verify_loop import run_fix_verify_loop
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings
from backend.storage.code_index import _rrf_fuse, _mmr_path_diverse


def test_fix_verify_respects_sprint_cancel_between_rounds():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableFixVerifyLoop": True,
            "requireCleanLint": True,
            "maxFixVerifyRounds": 3,
        }
    )
    task = init_new_task({"id": "T-FV-1", "title": "Lint fix", "description": "d"})
    normalize_task(task)
    state.SPRINT_CANCEL = False

    calls = {"n": 0}

    def execute_step(prompt, max_iterations=4):
        calls["n"] += 1
        state.SPRINT_CANCEL = True
        return "did work"

    agent = MagicMock()
    agent.execute_step.side_effect = execute_step

    lint_dirty = MagicMock()
    lint_dirty.outcome = "fail"
    lint_dirty.diagnostics = [{"severity": "error", "message": "x", "file": "a.py", "line": 1}]
    lint_dirty.summary = "1 error"

    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value="echo lint",
    ), patch(
        "backend.services.fix_verify_loop.run_workspace_command",
        return_value=lint_dirty,
    ), patch(
        "backend.services.fix_verify_loop.maybe_fanout_lint_diagnostics",
        return_value={"kept": lint_dirty.diagnostics, "spawned": []},
    ), patch(
        "backend.services.fix_verify_loop.find_task_by_id",
        return_value=task,
    ):
        out = run_fix_verify_loop(agent, task, "prompt", max_iterations=4)

    assert "aborted" in out.lower() or "cancelled" in out.lower() or calls["n"] == 1
    assert calls["n"] == 1  # second round blocked by SPRINT_CANCEL
    assert isinstance(task.get("lastCommandDiagnostics"), list)


def test_fix_verify_clean_on_first_round_stops():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableFixVerifyLoop": True, "requireCleanLint": True, "maxFixVerifyRounds": 3})
    task = init_new_task({"id": "T-FV-2", "title": "Clean", "description": "d"})
    normalize_task(task)
    state.SPRINT_CANCEL = False
    agent = MagicMock()
    agent.execute_step.return_value = "ok"
    lint_ok = MagicMock()
    lint_ok.outcome = "ok"
    lint_ok.diagnostics = []
    lint_ok.summary = "clean"
    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value="echo lint",
    ), patch(
        "backend.services.fix_verify_loop.run_workspace_command",
        return_value=lint_ok,
    ), patch(
        "backend.services.fix_verify_loop.find_task_by_id",
        return_value=task,
    ):
        out = run_fix_verify_loop(agent, task, "prompt", max_iterations=4)
    assert out == "ok"
    assert agent.execute_step.call_count == 1


def test_fix_verify_aborts_on_hard_stop_without_extra_round():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableFixVerifyLoop": True,
            "requireCleanLint": True,
            "maxFixVerifyRounds": 3,
            "fixVerifyAbortOnHardStop": True,
        }
    )
    task = init_new_task({"id": "T-FV-HARD", "title": "Hard stop", "description": "d"})
    normalize_task(task)
    state.SPRINT_CANCEL = False
    agent = MagicMock()
    agent.execute_step.return_value = "Stopped: 4 tool failures this step (limit 4)."

    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value="flutter analyze",
    ), patch(
        "backend.services.fix_verify_loop.run_workspace_command",
    ) as lint_mock, patch(
        "backend.services.fix_verify_loop.find_task_by_id",
        return_value=task,
    ):
        out = run_fix_verify_loop(agent, task, "prompt", max_iterations=4)

    assert "tool failures" in out.lower()
    assert agent.execute_step.call_count == 1
    lint_mock.assert_not_called()


def test_rrf_hybrid_ranks_expected_path():
    dense = [
        {"path": "src/auth.py", "score": 0.9, "content": "login oauth", "startLine": 1, "endLine": 10},
        {"path": "src/other.py", "score": 0.5, "content": "misc", "startLine": 1, "endLine": 5},
    ]
    lexical = [
        {"path": "src/auth.py", "score": 2.0, "content": "login oauth", "startLine": 1, "endLine": 10},
        {"path": "README.md", "score": 1.0, "content": "docs", "startLine": 1, "endLine": 3},
    ]
    fused = _rrf_fuse(dense, lexical, limit=3)
    assert fused
    assert fused[0]["path"] == "src/auth.py"
    diverse = _mmr_path_diverse(fused, top_k=2)
    assert any(h["path"] == "src/auth.py" for h in diverse)


def test_ui_improvements_pack_markers():
    root = Path(__file__).resolve().parents[1]
    detail = (root / "frontend" / "src" / "components" / "TaskDetailModal.tsx").read_text(
        encoding="utf-8"
    )
    assert "action-first: Needs User resolve" in detail
    # Needs User block appears before Description section heading in source order
    assert detail.find("action-first: Needs User resolve") < detail.find(
        'CollapsibleSection title="Description"'
    )
    bar = (root / "frontend" / "src" / "components" / "SprintProgressBar.tsx").read_text(
        encoding="utf-8"
    )
    assert "Pause" in bar
    assert "Cancel" in bar
    assert "sprint-waiting-on-you" in bar
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "workflow-presets" in panel
    assert "Download JSONL" in panel
    assert "discord-bot-status" in panel
    assert "workflow-section-phone-discord" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Known limitations" in readme
    assert "/ah-pending" in readme
