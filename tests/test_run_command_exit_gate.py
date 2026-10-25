"""runCommandFailOnNonZeroExit and oracle recording for analyze findings."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_run_command_tool_success_exit_65_analyze():
    from backend.agents.tool_outcomes import run_command_tool_success

    output = (
        "[findings exit 65]\n"
        "Analyzing workspace...\n"
        "  error • bad • lib/main.dart:1:1 • undefined_identifier\n"
    )
    ws = {"runCommandFailOnNonZeroExit": True}
    assert not run_command_tool_success("flutter analyze", output, ws)


def test_oracle_fail_on_lint_findings(monkeypatch):
    from backend.services.card_ledger import maybe_record_tool_oracle

    writes: list[tuple[bool, str]] = []

    def fake_write(tid, passed, detail=""):
        writes.append((passed, detail))

    monkeypatch.setattr(
        "backend.services.card_ledger.oracle_override_enabled",
        lambda: True,
    )
    monkeypatch.setattr("backend.services.card_ledger.write_oracle", fake_write)

    task = {"id": "TASK-1"}
    output = "[findings exit 1]\nerror • x • lib/a.dart:1:1 • e\n"
    maybe_record_tool_oracle(
        task,
        tool_name="run_command",
        arguments={"command": "dart analyze"},
        tool_output=output,
        success=False,
    )
    assert writes
    assert writes[-1][0] is False

