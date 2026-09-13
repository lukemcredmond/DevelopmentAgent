"""Per-card ledger, ideation skip, oracle Done veto, same-task park, cutoff summarizer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import build_task_prompt_legacy
from backend.bootstrap import initialize
from backend.services import card_ledger as ledger
from backend.services.card_ledger import (
    apply_ideation_result,
    format_ledger_for_prompt,
    is_length_cutoff,
    next_work_key,
    oracle_blocks_done,
    overwrite_notes,
    record_next_work_visit,
    same_next_task_should_park,
    seed_ledger_from_task,
    should_run_ideation,
    summarize_truncated_generation,
    write_oracle,
)
from backend.services.sprint_speed_gates import same_next_task_should_park as gates_same
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    get_workflow_settings,
    reset_workflow_settings,
)


def _task(tmp_path, **extra):
    initialize()
    reset_workflow_settings()
    state.WORKSPACE_DIR = str(tmp_path)
    task = {
        "id": "T-LEDGER",
        "title": "Add login",
        "description": "Implement login with email and password.",
        "acceptanceCriteria": ["Form validates email", "Submit posts to /login"],
        "status": "In Progress",
        "files": [],
        "transcript": [],
        "decisions": [],
        **extra,
    }
    return task


def test_defaults_enable_gvs5h_flags():
    for key in (
        "enableCardLedger",
        "enableDevIdeation",
        "enableOracleDoneOverride",
        "enableSameNextTaskStop",
        "enableCutoffSummarizer",
    ):
        assert DEFAULT_WORKFLOW_SETTINGS.get(key) is True
    initialize()
    reset_workflow_settings()
    ws = get_workflow_settings()
    assert ws.get("enableCardLedger") is True


def test_ledger_write_and_prompt_injection_caps(tmp_path):
    task = _task(tmp_path)
    seed_ledger_from_task(task)
    overwrite_notes(task["id"], "x" * 20000)
    block = format_ledger_for_prompt(task, max_chars=6000)
    assert "CARD LEDGER" in block
    assert "PLAN:" in block
    assert "TASKS:" in block
    assert "Form validates email" in block
    assert len(block) <= 6000
    notes = ledger.read_file(task["id"], "notes.md")
    assert len(notes) <= ledger.MAX_NOTES_CHARS


def test_ideation_skipped_when_notes_or_files_exist(tmp_path):
    task = _task(tmp_path)
    seed_ledger_from_task(task)
    assert should_run_ideation(task) is True
    overwrite_notes(task["id"], "already brainstormed")
    assert should_run_ideation(task) is False

    task2 = _task(tmp_path, id="T-FILES", files=[{"path": "lib/a.dart", "action": "written"}])
    seed_ledger_from_task(task2)
    assert should_run_ideation(task2) is False

    task3 = _task(tmp_path, id="T-DONE", ideationDone=True)
    seed_ledger_from_task(task3)
    assert should_run_ideation(task3) is False


def test_apply_ideation_parses_sections(tmp_path):
    task = _task(tmp_path)
    seed_ledger_from_task(task)
    apply_ideation_result(
        task,
        "### NOTES\nCore difficulty is auth state.\n### NEXT\n- Write AuthRepository\n- Add widget test\n",
    )
    notes = ledger.read_file(task["id"], "notes.md")
    assert "auth state" in notes.lower()
    descs = [t["desc"] for t in ledger.load_tasks(task["id"])]
    assert any("AuthRepository" in d for d in descs)
    assert task.get("ideationDone") is True


def test_failed_oracle_blocks_done(tmp_path):
    task = _task(tmp_path)
    write_oracle(task["id"], passed=False, detail="pytest exit 1 AssertionError")
    task["lastOraclePassed"] = False
    blocked, reason = oracle_blocks_done(task)
    assert blocked is True
    assert "Oracle FAIL" in reason

    write_oracle(task["id"], passed=True, detail="pytest ok")
    task["lastOraclePassed"] = True
    blocked, reason = oracle_blocks_done(task)
    assert blocked is False

    write_oracle(task["id"], passed=False, detail="still failing")
    task["qaEvidence"] = {"userOverride": True}
    blocked, _ = oracle_blocks_done(task)
    assert blocked is False


def test_same_next_task_parks_without_writes(tmp_path):
    task = _task(tmp_path, focusMode="ac", focusAcIndex=0)
    key = next_work_key(task)
    assert key
    record_next_work_visit(task, writes_succeeded=0, oracle_passed=None)
    assert task.get("lastNextWorkNoWrite") is True
    assert gates_same(task) is True
    assert same_next_task_should_park(task) is True

    record_next_work_visit(task, writes_succeeded=1, oracle_passed=False)
    assert same_next_task_should_park(task) is False

    record_next_work_visit(task, writes_succeeded=0, oracle_passed=True)
    assert same_next_task_should_park(task) is False


def test_length_cutoff_summarizer_once(tmp_path):
    task = _task(tmp_path)
    seed_ledger_from_task(task)
    assert is_length_cutoff("length") is True
    assert is_length_cutoff("empty_generation_timeout") is False

    digest_msg = SimpleNamespace(content="Partial DFS approach; recursion leftover.")
    agent = SimpleNamespace(
        role="Developer",
        _skip_cutoff_summary=False,
        _cutoff_summarized=False,
        _chat=MagicMock(return_value=SimpleNamespace(message=digest_msg)),
    )
    digest = summarize_truncated_generation(
        agent, task, partial_text="thinking " * 40, done_reason="length"
    )
    assert "DFS" in digest or "recursion" in digest
    assert agent._chat.call_count == 1
    notes = ledger.read_file(task["id"], "notes.md")
    assert "cutoff" in notes.lower() or "DFS" in notes or "recursion" in notes

    digest2 = summarize_truncated_generation(
        agent, task, partial_text="more thinking", done_reason="length"
    )
    assert digest2 == ""
    assert agent._chat.call_count == 1


def test_empty_generation_is_not_a_length_cutoff():
    assert is_length_cutoff("") is False
    assert is_length_cutoff("stop") is False


def test_prompt_includes_ledger_before_transcript(tmp_path):
    task = _task(tmp_path)
    seed_ledger_from_task(task)
    overwrite_notes(task["id"], "Prefer range tree over naive scan.")
    task["transcript"] = [
        {"timestamp": "t", "agent": "Developer", "content": "old transcript line that should be tail"}
    ]
    prompt = build_task_prompt_legacy(task, "brief", agent_role="Developer")
    assert "CARD LEDGER" in prompt
    assert "range tree" in prompt
    assert prompt.index("CARD LEDGER") < prompt.index("TASK TRANSCRIPT")


def test_chat_still_does_not_retry_empty_generation():
    """Empty-gen remains a single provider.chat; summarizer must not open a retry path."""
    from unittest.mock import patch

    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 4,
            "ollamaRetryDelaySec": [0, 0, 0, 0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetrySec": 0,
            "ollamaCooldownRetryAttempts": 2,
        }
    )
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    mock_provider = MagicMock()
    mock_provider.chat.side_effect = Exception("Ollama empty generation timed out after 90s")
    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert mock_provider.chat.call_count == 1


def test_execute_step_with_active_task_does_not_unboundlocal():
    """Regression: inner import of find_task_by_id must not shadow the module-level name."""
    from unittest.mock import patch

    from backend.agents.registry import agent_dev

    initialize()
    reset_workflow_settings()
    task = {
        "id": "T-UNBOUND",
        "title": "Fix unbound",
        "description": "desc",
        "acceptanceCriteria": ["ok"],
        "status": "In Progress",
        "files": [],
        "transcript": [],
        "decisions": [],
    }
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    state.ACTIVE_SPRINT_TASK_ID = "T-UNBOUND"
    state.ACTIVE_SPRINT_AGENT = "Developer"

    class _Msg:
        content = "done"
        tool_calls = None

    class _Resp:
        message = _Msg()

    with patch.object(agent_dev, "_chat", return_value=_Resp()), patch(
        "backend.agents.registry.configure_agent_tools"
    ), patch(
        "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
    ), patch.object(
        agent_dev, "_build_system_content", return_value="sys"
    ), patch.object(
        agent_dev, "_build_user_content", return_value="user"
    ):
        result = agent_dev.execute_step("implement", max_iterations=1)

    assert result is not None
    state.ACTIVE_SPRINT_TASK_ID = None


def test_storage_connect_uses_timeout_and_wal(tmp_path):
    import sqlite3

    from backend.storage.project_storage import SQLITE_TIMEOUT_SEC, ProjectStorage

    seen: dict = {}
    real_connect = sqlite3.connect

    def wrapped(path, timeout=0, **kwargs):
        seen["timeout"] = timeout
        return real_connect(path, timeout=timeout, **kwargs)

    from unittest.mock import patch

    db = tmp_path / "scrum.db"
    with patch("backend.storage.project_storage.sqlite3.connect", side_effect=wrapped):
        store = ProjectStorage(str(db))
        store.set_setting("k", "v")
        assert store.get_setting("k") == "v"
    assert seen.get("timeout") == SQLITE_TIMEOUT_SEC
    conn = sqlite3.connect(str(db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert str(mode).lower() == "wal"


def test_storage_retries_once_on_locked(tmp_path):
    import sqlite3

    from backend.storage.project_storage import ProjectStorage

    db = tmp_path / "scrum.db"
    store = ProjectStorage(str(db))
    calls = {"n": 0}
    real_write = store._connect

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_write()

    from unittest.mock import patch

    with patch.object(store, "_connect", side_effect=flaky):
        store.set_setting("retry-key", "ok")
    assert calls["n"] == 2
    assert store.get_setting("retry-key") == "ok"
