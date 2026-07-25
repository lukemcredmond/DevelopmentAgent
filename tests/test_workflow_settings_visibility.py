"""Workflow defaults and WorkflowPanel visibility markers."""

from __future__ import annotations

from pathlib import Path

from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS


def test_allow_chained_commands_default_on():
    assert DEFAULT_WORKFLOW_SETTINGS.get("allowChainedCommands") is True


def test_workflow_panel_chain_and_missing_settings_visible():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Allow safe command chaining" in panel
    assert "allowChainedCommands !== false" in panel
    # Chaining must not sit only inside requireToolApproval-gated JSX for its checkbox
    chain_idx = panel.index("Allow safe command chaining")
    # Nearest preceding requireToolApproval block should have closed before the chain label
    closed_before = panel.rfind(")}", 0, chain_idx)
    assert closed_before > 0
    assert "Auto-start sprint after plan" in panel
    assert "Max stuck steps" in panel
    assert "Max tool failures/step" in panel
    assert "Ollama retry delays" in panel
    assert "Tools requiring approval" in panel
    assert "MCP servers (JSON array)" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "allowChainedCommands" in readme
    assert "**On**" in readme or "| **On** |" in readme or "default **On**" in readme.lower() or "| **On**" in readme
    assert "Always visible under Settings → Workflow" in readme
