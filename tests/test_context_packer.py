"""Tests for optional Repomix/code2prompt context packer."""

from unittest.mock import MagicMock, patch

from backend.services.context_packer import run_context_pack


def test_context_packer_off_returns_empty():
    with patch("backend.services.context_packer.get_workflow_settings") as gs:
        gs.return_value = {"contextPacker": "off"}
        assert run_context_pack(["src/"]) == ""


def test_context_packer_repomix_success():
    with patch("backend.services.context_packer.get_workflow_settings") as gs:
        gs.return_value = {
            "contextPacker": "repomix",
            "contextPackerMaxChars": 5000,
            "terminalTimeoutSec": 60,
            "repomixCommand": "repomix",
        }
        with patch("backend.services.context_packer.Path") as path_cls:
            path_cls.return_value.resolve.return_value.is_dir.return_value = True
            with patch("backend.services.context_packer.subprocess.run") as run:
                run.return_value = MagicMock(returncode=0, stdout="packed tree", stderr="")
                out = run_context_pack(["src/main.py"], mode="repomix")
                assert out == "packed tree"
                run.assert_called_once()


def test_context_packer_failure_returns_empty():
    with patch("backend.services.context_packer.get_workflow_settings") as gs:
        gs.return_value = {
            "contextPacker": "repomix",
            "contextPackerMaxChars": 5000,
            "terminalTimeoutSec": 60,
            "repomixCommand": "repomix",
        }
        with patch("backend.services.context_packer.Path") as path_cls:
            path_cls.return_value.resolve.return_value.is_dir.return_value = True
            with patch("backend.services.context_packer.subprocess.run") as run:
                run.return_value = MagicMock(returncode=1, stdout="", stderr="fail")
                assert run_context_pack(["."]) == ""
