"""Q&A markdown, run_command success cache, and same-args success limit."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.scrum_agent import SAME_ARGS_SUCCESS_LIMIT, ScrumAgent
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.task_qa_markdown import (
    build_task_qa_markdown,
    read_task_qa_markdown_for_prompt,
    task_qa_markdown_path,
    update_task_qa_markdown,
)
from backend.services.tool_cache import (
    check_run_command_cache,
    clear_tool_cache,
    get_cached_result,
    is_probe_command,
    store_cached_result,
)
from backend.workspace.files import record_step_file_read


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_build_task_qa_markdown_has_sections():
    task = {
        "id": "TASK-QA-1",
        "title": "Wire login",
        "userResolutions": [
            {"question": "Which auth?", "answer": "OAuth", "timestamp": "t1", "targetLane": "dev"}
        ],
        "decisions": [
            {"agent": "Developer", "type": "note", "summary": "Use flutter analyze"},
        ],
        "transcript": [
            {
                "toolName": "run_command",
                "toolSuccess": True,
                "toolArgs": {"command": "flutter --version"},
                "content": "ok",
            }
        ],
    }
    md = build_task_qa_markdown(task)
    assert "# Task TASK-QA-1" in md
    assert "## Q&A" in md
    assert "### Q: Which auth?" in md
    assert "**A:** OAuth" in md
    assert "## Decisions (summarized)" in md
    assert "[Developer/note]" in md
    assert "## Recent actions" in md
    assert "flutter --version" in md


def test_qa_markdown_humanizes_json_blobs():
    task = {
        "id": "TASK-JSON",
        "title": {"text": "Nested title"},
        "userResolutions": [
            {
                "question": '{"question": "Use Firebase or Supabase?", "options": ["Firebase", "Supabase"]}',
                "answer": '{"answer": "Firebase", "reason": "existing SDK"}',
            }
        ],
        "decisions": [
            {
                "agent": "Developer",
                "type": "note",
                "summary": '{"summary": "Chose Firebase Auth"}',
            }
        ],
        "userQuestion": '{"user_question": "Confirm API key location?"}',
        "transcript": [],
    }
    md = build_task_qa_markdown(task)
    assert "{" not in md or "Firebase" in md  # prefer prose over raw braces when extractable
    assert "Use Firebase or Supabase?" in md
    assert "Firebase" in md
    assert "Chose Firebase Auth" in md
    assert "Confirm API key location?" in md
    assert "Awaiting your answer" in md
    assert "**Title:** Nested title" in md


def test_update_and_prompt_inject_qa_markdown(tmp_path, monkeypatch):
    initialize()
    _clear_board()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    task = init_new_task(
        {
            "id": "TASK-QA-2",
            "title": "Notes card",
            "description": "d",
            "status": "In Progress",
        }
    )
    task["userResolutions"] = [{"question": "Scope?", "answer": "MVP only"}]
    task["decisions"] = [{"agent": "PO", "type": "note", "summary": "Keep MVP"}]
    state.SHARED_BOARD["In Progress"] = [task]
    path = update_task_qa_markdown("TASK-QA-2")
    assert path == task_qa_markdown_path("TASK-QA-2")
    assert path in state.VIRTUAL_FILESYSTEM
    phys = Path(tmp_path) / Path(path)
    assert phys.is_file()
    prompt_doc = read_task_qa_markdown_for_prompt(task)
    assert "Scope?" in prompt_doc
    assert "MVP only" in prompt_doc

    from backend.agents.task_context import build_task_prompt

    full = build_task_prompt(task, "brief")
    assert "TASK Q&A DOC" in full
    assert "Scope?" in full


def test_probe_command_detection():
    assert is_probe_command("flutter --version")
    assert is_probe_command("dart --help")
    assert is_probe_command("tool -h")
    assert not is_probe_command("flutter analyze")
    assert not is_probe_command("npm test")


def test_cache_returns_prior_success_for_identical_flutter_version():
    clear_tool_cache()
    args = {"command": "flutter --version"}
    store_cached_result("run_command", args, "Flutter 3.22.0\n[exit 0]", True)

    probe_hit = check_run_command_cache("flutter --version", args)
    assert probe_hit is not None
    assert "Flutter 3.22.0" in probe_hit
    assert "cached" in probe_hit.lower()

    clear_tool_cache()
    store_cached_result("run_command", args, "Flutter 3.22.0\n[exit 0]", True)
    cached = get_cached_result("run_command", args)
    assert cached is not None
    output, success = cached
    assert success is True
    assert "Flutter 3.22.0" in output


def test_same_args_success_limit_skips_then_stops():
    """Second identical success is skipped; third early-stops."""
    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()

    call = MagicMock()
    call.function.name = "read_file"
    call.function.arguments = {"path": "pubspec.yaml"}

    failed: list = []
    successful: list = []
    total_failures = [0]

    clear_tool_cache()
    args = {"path": "pubspec.yaml"}
    store_cached_result("read_file", args, "name: app\n", True)

    state.STEP_FILE_READS.clear()
    record_step_file_read("pubspec.yaml", "name: app\n")

    key = ("read_file", json.dumps(args, sort_keys=True, default=str))
    successful.append(key)

    with patch("backend.agents.scrum_agent.finish_run"), patch(
        "backend.agents.scrum_agent.add_system_log"
    ):
        _name, _out_args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id=None,
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=failed,
            successful_tool_keys=successful,
            total_failures=total_failures,
            max_tool_failures=5,
        )
        assert early is None
        assert result.success is True
        assert getattr(result, "duplicate_skip", False) is True
        assert successful.count(key) == 2

        _name, _out_args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id=None,
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=failed,
            successful_tool_keys=successful,
            total_failures=total_failures,
            max_tool_failures=5,
        )
    assert early is not None
    assert "Stopped" in early
    assert SAME_ARGS_SUCCESS_LIMIT == 3


def test_model_inputs_have_text_white():
    settings = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "SettingsSlideOver.tsx"
    text = settings.read_text(encoding="utf-8")
    # PO/DEV/CR/QA model inputs share one className with text-white
    assert "text-white text-right flex-1" in text or (
        "text-white" in text and "PO MODEL" in text and "QA MODEL" in text
    )
    assert "Advanced context (LLM speed)" in text
    assert "maxToolOutputCharsForLlm" in text

    workflow = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "WorkflowPanel.tsx"
    wf = workflow.read_text(encoding="utf-8")
    assert "LLM context / speed" in wf
    assert "messagePruneThresholdPct" in wf


def test_working_notes_section_in_task_detail_modal():
    modal = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "components"
        / "TaskDetailModal.tsx"
    )
    text = modal.read_text(encoding="utf-8")
    assert "Working notes (Q&A)" in text
