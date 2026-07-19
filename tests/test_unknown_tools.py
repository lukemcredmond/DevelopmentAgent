"""Unknown / gated tool handling and built-in aliases."""

from backend import state
from backend.agents.registry import agent_dev, agent_po, configure_agent_tools
from backend.bootstrap import initialize
from backend.services.tool_aliases import (
    list_pending_tools,
    resolve_tool_call,
)


def _pending_count() -> int:
    return len(list_pending_tools())


def test_refinement_write_file_no_pending_modal():
    initialize()
    state.PENDING_TOOL_REQUESTS.clear()
    state.REFINEMENT_MODE = True
    state.ACTIVE_SPRINT_AGENT = "Developer"
    configure_agent_tools()
    before = _pending_count()
    result = agent_dev.registry.invoke("write_file", {"path": "a.py", "content": "x"})
    assert "refinement" in result.lower()
    assert "Error:" in result
    assert _pending_count() == before
    state.REFINEMENT_MODE = False
    configure_agent_tools()


def test_po_write_file_no_pending_modal():
    initialize()
    state.PENDING_TOOL_REQUESTS.clear()
    state.REFINEMENT_MODE = False
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    configure_agent_tools()
    before = _pending_count()
    result = agent_po.registry.invoke("write_file", {"path": "a.py", "content": "x"})
    assert "not available" in result.lower() or "Product Owner" in result
    assert "Error:" in result
    assert "Map it in the Tool Resolution" not in result
    assert _pending_count() == before


def test_create_file_alias_resolves_to_write_file():
    name, args, was = resolve_tool_call("create_file", {"path": "x.py", "content": "hi"})
    assert was is True
    assert name == "write_file"
    assert args["path"] == "x.py"


def test_write_capital_alias_resolves():
    name, _args, was = resolve_tool_call("Write", {"path": "x.py", "content": "hi"})
    assert was is True
    assert name == "write_file"


def test_builtin_alias_invokes_when_dev_has_write(tmp_path, monkeypatch):
    initialize()
    state.REFINEMENT_MODE = False
    state.ACTIVE_SPRINT_AGENT = "Developer"
    configure_agent_tools()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.PENDING_TOOL_REQUESTS.clear()
    before = _pending_count()
    result = agent_dev.registry.invoke("create_file", {"path": "hello.txt", "content": "hi"})
    assert "Map it in the Tool Resolution" not in result
    assert _pending_count() == before
    assert (tmp_path / "hello.txt").exists() or "Successfully" in result or "saved" in result.lower()


def test_true_invent_still_queues_pending():
    initialize()
    state.PENDING_TOOL_REQUESTS.clear()
    state.REFINEMENT_MODE = False
    state.ACTIVE_SPRINT_AGENT = "Developer"
    configure_agent_tools()
    before = _pending_count()
    result = agent_dev.registry.invoke("flutter_analyze_magic", {"x": "1"})
    assert "not registered" in result.lower()
    assert "Map it" in result
    assert _pending_count() == before + 1
