"""PO Needs PO clarification apply/move, fingerprints, and exit reasons."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.po_clarification import (
    complete_needs_po_clarification,
    extract_json_object_from_text,
    prune_repeated_po_json,
)
from backend.services.sprint_speed_gates import record_consecutive_bad_exit
from backend.services.step_diagnostics import derive_exit_reason


def _needs_po_task(task_id: str = "T-PO-FAST") -> dict:
    initialize()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
        "Blocked",
    ):
        state.SHARED_BOARD.setdefault(lane, [])
        state.SHARED_BOARD[lane] = [t for t in state.SHARED_BOARD[lane] if t.get("id") != task_id]
    task = init_new_task(
        {"id": task_id, "title": "Club card", "description": "vague", "status": "Needs PO"}
    )
    state.SHARED_BOARD["Needs PO"] = [task]
    return task


def test_extract_json_from_prose_fence():
    text = (
        "Moved to In Progress.\n"
        '```json\n{"description": "Show image", "acceptanceCriteria": ["path null-safe"]}\n```\n'
    )
    obj = extract_json_object_from_text(text)
    assert obj["description"] == "Show image"
    assert obj["acceptanceCriteria"] == ["path null-safe"]


def test_extract_json_brace_scan_in_prose():
    text = 'Here you go {"description": "x", "acceptanceCriteria": ["a"]} thanks'
    obj = extract_json_object_from_text(text)
    assert obj["description"] == "x"


def test_json_text_moves_needs_po_to_in_progress():
    task = _needs_po_task()
    ok, msg = complete_needs_po_clarification(
        task["id"],
        text='{"description": "Display club card image", "acceptanceCriteria": ["shown when path set", "null path safe"]}',
    )
    assert ok is True
    assert "In Progress" in msg
    assert task["id"] in [t["id"] for t in state.SHARED_BOARD.get("In Progress", [])]
    assert task["description"] == "Display club card image"


def test_update_board_args_apply_clarification():
    task = _needs_po_task("T-PO-ARGS")
    ok, msg = complete_needs_po_clarification(
        task["id"],
        board_args={
            "task_id": task["id"],
            "target_lane": "In Progress",
            "description": "From board args",
            "acceptanceCriteria": ["ac1", "ac2"],
        },
    )
    assert ok is True
    assert task["description"] == "From board args"
    assert "In Progress" in msg


def test_identical_fingerprint_still_moves():
    task = _needs_po_task("T-PO-FP")
    payload = '{"description": "same", "acceptanceCriteria": ["one", "two"]}'
    complete_needs_po_clarification(task["id"], text=payload)
    # Simulate stuck card still in Needs PO with same JSON
    state.SHARED_BOARD["In Progress"] = [
        t for t in state.SHARED_BOARD.get("In Progress", []) if t.get("id") != task["id"]
    ]
    state.SHARED_BOARD["Needs PO"] = [task]
    task["status"] = "Needs PO"
    ok, _msg = complete_needs_po_clarification(task["id"], text=payload)
    assert ok is True
    assert int(task.get("identicalPoClarificationCount") or 0) >= 2


def test_needs_po_no_advance_is_not_fix_verify_done():
    assert (
        derive_exit_reason(
            agent_result='{"description": "x"}',
            tools_used=set(),
            lane_before="Needs PO",
            lane_after="Needs PO",
        )
        == "po_clarification_incomplete"
    )
    assert (
        derive_exit_reason(
            agent_result="ok",
            tools_used=set(),
            lane_before="Needs PO",
            lane_after="In Progress",
        )
        == "po_clarified"
    )


def test_po_incomplete_counts_as_bad_exit():
    task: dict = {}
    record_consecutive_bad_exit(task, "po_clarification_incomplete")
    record_consecutive_bad_exit(task, "po_clarification_incomplete")
    assert task["consecutiveBadExits"] == 2
    record_consecutive_bad_exit(task, "po_clarified", progress_made=True)
    assert task.get("consecutiveBadExits") == 0


def test_po_turn_hit_generation_cap():
    from backend.services.po_clarification import po_turn_hit_generation_cap

    assert po_turn_hit_generation_cap(
        eval_tokens=1024, num_predict=1024, tool_names=[], content=""
    )
    assert po_turn_hit_generation_cap(
        eval_tokens=2048, num_predict=2048, tool_names=[], content="thinking..."
    )
    assert not po_turn_hit_generation_cap(
        eval_tokens=2048,
        num_predict=2048,
        tool_names=["update_board"],
        content="",
    )
    assert not po_turn_hit_generation_cap(
        eval_tokens=400,
        num_predict=2048,
        tool_names=[],
        content="",
    )
    json_ok = '{"description": "Show image", "acceptanceCriteria": ["null-safe"]}'
    assert not po_turn_hit_generation_cap(
        eval_tokens=2048, num_predict=2048, tool_names=[], content=json_ok
    )


def test_truncated_and_incomplete_exit_reasons():
    assert (
        derive_exit_reason(
            agent_result="Stopped: PO generation truncated without clarification JSON or update_board.",
            tools_used=set(),
            lane_before="Needs PO",
            lane_after="Needs PO",
        )
        == "po_generation_truncated"
    )
    assert (
        derive_exit_reason(
            agent_result="Stopped: PO clarification incomplete — no JSON or update_board.",
            tools_used=set(),
            lane_before="Needs PO",
            lane_after="Needs PO",
        )
        == "po_clarification_incomplete"
    )


def test_prune_repeated_po_json():
    blob = '{"description": "x", "acceptanceCriteria": ["a"]}'
    assert "already recorded" in prune_repeated_po_json(blob)


def test_needs_po_work_items_are_po_not_dev():
    from backend.agents.task_context import get_task_lane
    from backend.services.agent_work_items import derive_agent_work_items

    task = _needs_po_task("T-PO-WI")
    assert get_task_lane(task["id"]) == "Needs PO"
    items = derive_agent_work_items(task)
    ids = {i["id"] for i in items}
    assert "clarify:json" in ids
    assert "write:implement" not in ids


def test_duplicate_skip_logged_in_tools_log(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    from backend.agents.scrum_agent import _log_duplicate_skip
    from backend.services.step_diagnostics import clear_active_step_trace, start_step_trace

    trace = start_step_trace("T-SKIP", "t", "Product Owner", "Needs PO")
    _log_duplicate_skip(
        agent="Product Owner",
        tool_name="update_board",
        arguments={"task_id": "T-SKIP", "target_lane": "In Progress"},
        tool_output="skipped",
        task_id="T-SKIP",
        run_id="r1",
        success=True,
    )
    names = [e.get("toolName") for e in trace.tools_log]
    assert "update_board" in names
    assert "skipped" in (trace.tools_log[0].get("summary") or "")
    clear_active_step_trace()


def test_ollama_ms_capped_to_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    from datetime import datetime, timedelta

    from backend.services.step_diagnostics import clear_active_step_trace, start_step_trace

    trace = start_step_trace("T-MS", "t", "Product Owner", "Needs PO")
    trace.started_monotonic = datetime.now() - timedelta(seconds=1)
    trace.log_ollama_call(1, duration_ms=5000)
    trace.log_ollama_call(2, duration_ms=5000)
    payload = trace._build_payload(status="running")
    assert payload["ollamaMsTotal"] <= payload["durationMs"]
    assert payload.get("ollamaMsCapped") is True
    clear_active_step_trace()


def test_first_runnable_needs_po_skips_circuit_latched():
    from backend import state
    from backend.services.sprint_service import _first_runnable_needs_po
    from backend.services.sprint_speed_gates import latch_needs_po_auto_skip

    skipped = _needs_po_task("T-PO-SKIP")
    latch_needs_po_auto_skip(skipped, reason="empty")
    runnable = init_new_task(
        {"id": "T-PO-RUN", "title": "Next", "description": "ok", "status": "Needs PO"}
    )
    state.SHARED_BOARD["Needs PO"] = [skipped, runnable]
    picked = _first_runnable_needs_po()
    assert picked is not None
    assert picked.get("id") == "T-PO-RUN"
