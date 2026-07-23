"""Tests for agent shell command timeout selection."""

from __future__ import annotations

from unittest.mock import patch

from backend.config import LONG_COMMAND_TIMEOUT_SEC, TERMINAL_TIMEOUT_SEC
from backend.services.command_result import (
    is_long_running_command,
    resolve_command_timeout,
)


def test_default_timeout_is_at_least_120():
    assert TERMINAL_TIMEOUT_SEC >= 120
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"terminalTimeoutSec": 120},
    ):
        assert resolve_command_timeout("flutter analyze") == 120


def test_build_runner_gets_long_timeout():
    assert is_long_running_command("flutter pub run build_runner build --delete-conflicting-outputs")
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"terminalTimeoutSec": 120},
    ):
        assert resolve_command_timeout(
            "flutter pub run build_runner build --delete-conflicting-outputs"
        ) == LONG_COMMAND_TIMEOUT_SEC


def test_workflow_setting_raises_base():
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"terminalTimeoutSec": 300},
    ):
        assert resolve_command_timeout("pytest -q") == 300


def test_explicit_timeout_wins():
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"terminalTimeoutSec": 120},
    ):
        assert resolve_command_timeout("flutter pub run build_runner build", explicit=90) == 90


def test_long_command_uses_max_of_setting_and_600():
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"terminalTimeoutSec": 900},
    ):
        assert resolve_command_timeout("dotnet build") == 900
