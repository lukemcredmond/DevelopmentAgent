"""FLUTTER_VERIFY_PRESET normalizes as a shell custom tool."""

from __future__ import annotations

from backend.services.custom_tools import FLUTTER_VERIFY_PRESET, normalize_custom_tool_def


def test_flutter_verify_preset_normalizes():
    norm = normalize_custom_tool_def(FLUTTER_VERIFY_PRESET)
    assert norm is not None
    assert norm["name"] == "flutter_verify"
    assert "dart analyze" in str(norm.get("shell", {}).get("command") or "")
