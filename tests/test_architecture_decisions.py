"""Architecture decision records (ADR) per card."""

import json
from unittest.mock import MagicMock, patch

from backend.agents.task_context import init_new_task, normalize_task
from backend.services.architecture_decisions import (
    append_architecture_decision,
    format_architecture_decisions_for_prompt,
    normalize_adr_topic,
    validate_adr_payload,
)
from backend.services.task_architecture_markdown import build_task_architecture_markdown as build_md


def test_normalize_adr_topic():
    assert normalize_adr_topic("Meal List State") == "meal-list-state"


def test_append_and_supersede_chain():
    from backend import state

    task = init_new_task(
        {
            "id": "T-ADR",
            "title": "Test",
            "description": "d",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]

    payload1 = {
        "topic": "state-mgmt",
        "title": "Use Riverpod",
        "context": "Need reactive meal list",
        "decision": "Riverpod Notifier for meals",
        "alternatives": [{"name": "Provider", "rejectedBecause": "Less ergonomic for this app"}],
    }
    r1, err1 = append_architecture_decision("T-ADR", payload1, author="Developer")
    assert not err1 and r1
    assert r1["status"] == "accepted"

    payload2 = {
        "topic": "state-mgmt",
        "title": "Use Riverpod v2 pattern",
        "context": "Simplify testing",
        "decision": "AsyncNotifier pattern",
        "alternatives": [{"name": "Keep sync Notifier", "rejectedBecause": "Harder to test imports"}],
        "changeNote": "Testing and async DB reads drove the change",
    }
    r2, err2 = append_architecture_decision("T-ADR", payload2, author="Developer")
    assert not err2 and r2
    live = state.SHARED_BOARD["In Progress"][0]
    adrs = live["architectureDecisions"]
    accepted = [a for a in adrs if a["status"] == "accepted"]
    superseded = [a for a in adrs if a["status"] == "superseded"]
    assert len(accepted) == 1
    assert len(superseded) == 1
    assert accepted[0]["decision"].startswith("AsyncNotifier")


def test_validate_requires_alternative():
    ok, err = validate_adr_payload({"decision": "x", "topic": "t", "alternatives": []})
    assert not ok
    assert "alternative" in err.lower()


def test_format_for_prompt_includes_alternatives():
    task = {
        "architectureDecisions": [
            {
                "id": "ADR-1",
                "topic": "db",
                "title": "Drift",
                "status": "accepted",
                "context": "Offline",
                "decision": "Use Drift",
                "alternatives": [{"name": "sqflite raw", "rejectedBecause": "More boilerplate"}],
            }
        ]
    }
    text = format_architecture_decisions_for_prompt(task)
    assert "ARCHITECTURE DECISIONS" in text
    assert "Drift" in text
    assert "sqflite" in text


def test_build_markdown_sections():
    task = {
        "id": "T-1",
        "title": "Card",
        "architectureDecisions": [
            {
                "id": "ADR-A",
                "topic": "ui",
                "title": "Material",
                "status": "accepted",
                "author": "Dev",
                "createdAt": "2026-01-01",
                "updatedAt": "2026-01-01",
                "context": "c",
                "decision": "Material 3",
                "alternatives": [],
            }
        ],
    }
    md = build_md(task)
    assert "Architecture decisions" in md
    assert "Material" in md


def test_api_add_architecture_decision_route():
    from fastapi.testclient import TestClient

    from backend.bootstrap import initialize
    from backend import state
    from backend.main import app

    initialize()
    for lane in ("In Progress", "Backlog", "Done", "Needs PO", "Needs User"):
        state.SHARED_BOARD.setdefault(lane, [])
    task = init_new_task(
        {"id": "T-API-ADR", "title": "API card", "description": "", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-API-ADR/architecture-decisions",
        json={
            "topic": "api-shape",
            "title": "REST local only",
            "context": "Offline app",
            "decision": "No HTTP client on startup",
            "alternatives": [{"name": "GraphQL", "rejectedBecause": "No server"}],
        },
    )
    assert resp.status_code == 200
    live = next(t for t in resp.json()["board"]["In Progress"] if t["id"] == "T-API-ADR")
    assert len(live.get("architectureDecisions") or []) == 1


def test_record_architecture_decision_tool_registered():
    from backend.agents.registry import BUILTIN_TOOL_CATALOG

    assert "record_architecture_decision" in BUILTIN_TOOL_CATALOG


def test_maybe_extract_skips_without_writes():
    from backend import state
    from backend.services.architecture_decisions import maybe_extract_architecture_decisions

    task = init_new_task(
        {"id": "T-NO-WRITE", "title": "x", "description": "", "status": "In Progress", "files": []}
    )
    state.SHARED_BOARD["In Progress"] = [task]
    assert maybe_extract_architecture_decisions("T-NO-WRITE") == 0


def test_maybe_extract_parses_mock_llm():
    from backend import state
    from backend.services.architecture_decisions import maybe_extract_architecture_decisions

    task = init_new_task(
        {
            "id": "T-EX",
            "title": "x",
            "description": "build ui",
            "status": "In Progress",
        }
    )
    task["files"] = [{"path": "lib/main.dart", "action": "written"}]
    state.SHARED_BOARD["In Progress"] = [task]
    mock_msg = MagicMock()
    mock_msg.content = json.dumps(
        {
            "decisions": [
                {
                    "topic": "ui-shell",
                    "title": "Single scaffold",
                    "context": "MVP",
                    "decision": "One MaterialApp scaffold",
                    "alternatives": [
                        {"name": "Multi navigator", "rejectedBecause": "Overkill for MVP"}
                    ],
                }
            ]
        }
    )
    mock_resp = MagicMock(message=mock_msg)
    with (
        patch("backend.agents.registry.agent_dev._chat", return_value=mock_resp),
        patch(
            "backend.services.architecture_decisions.adr_post_step_extract_enabled",
            return_value=True,
        ),
    ):
        n = maybe_extract_architecture_decisions("T-EX", exit_reason="completed_with_writes")
    assert n == 1
    live = state.SHARED_BOARD["In Progress"][0]
    assert len(live.get("architectureDecisions") or []) == 1
