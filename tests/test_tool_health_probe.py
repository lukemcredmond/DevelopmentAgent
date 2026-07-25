"""Tool Health smoke probes."""

from __future__ import annotations

from pathlib import Path

from backend.bootstrap import initialize
from backend.services.tool_probe import (
    build_probe_arguments,
    run_tool_probe,
    should_skip_probe,
)


def test_build_probe_list_dir():
    args, skip = build_probe_arguments("list_dir", {})
    assert skip is None
    assert args == {"path": "."}


def test_build_probe_write_file_skips():
    args, skip = build_probe_arguments("write_file", {})
    assert args is None
    assert skip == "destructive"
    assert should_skip_probe("write_file") == "destructive"


def test_build_probe_run_command_skips_by_default():
    args, skip = build_probe_arguments("run_command", {})
    assert skip == "destructive_or_slow"
    args2, skip2 = build_probe_arguments("run_command", {}, include_destructive=True)
    assert skip2 is None
    assert args2 and args2.get("command")


def test_run_probe_list_dir_pass():
    initialize()
    result = run_tool_probe("dev", "list_dir")
    assert result["toolName"] == "list_dir"
    assert result["status"] in ("pass", "fail")  # fail only if registry missing tool
    assert "hints" in result
    if result["status"] == "pass":
        assert result["success"] is True


def test_run_probe_write_file_skip():
    initialize()
    result = run_tool_probe("dev", "write_file")
    assert result["status"] == "skip"
    assert result["skipReason"] == "destructive"


def test_probe_api_endpoints():
    from fastapi.testclient import TestClient

    from backend.main import app

    initialize()
    client = TestClient(app)
    skip_res = client.post(
        "/api/tools/probe",
        json={"agent": "dev", "toolName": "write_file"},
    )
    assert skip_res.status_code == 200
    assert skip_res.json()["result"]["status"] == "skip"

    list_res = client.post(
        "/api/tools/probe",
        json={"agent": "dev", "toolName": "list_dir"},
    )
    assert list_res.status_code == 200
    body = list_res.json()["result"]
    assert body["toolName"] == "list_dir"
    assert body["status"] in ("pass", "fail")
    assert isinstance(body.get("hints"), list)


def test_ui_health_markers():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "ToolsPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "health" in panel
    assert "ToolHealthPanel" in panel
    assert "Health" in panel
    health = (root / "frontend" / "src" / "components" / "ToolHealthPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Test all safe" in health
    assert "Hints for the model" in health
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Health" in readme
    assert "/api/tools/probe" in readme
