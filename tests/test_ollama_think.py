"""Ollama think policy, aux chat options, and phase routing with single-model mode."""

from backend.bootstrap import initialize
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    get_workflow_settings,
    reset_workflow_settings,
    resolve_ollama_think,
    save_workflow_settings,
)


def test_resolve_ollama_think_defaults_false():
    initialize()
    reset_workflow_settings()
    assert resolve_ollama_think("Developer") is False
    assert resolve_ollama_think("Product Owner") is False


def test_resolve_ollama_think_plan_outline_write_when_enabled():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"ollamaThinkPlanOutlineWrite": True})
    assert (
        resolve_ollama_think(
            "Product Owner",
            plan_outline_no_tools=True,
            plan_outline_phase="write",
        )
        is True
    )
    assert (
        resolve_ollama_think(
            "Product Owner",
            plan_outline_no_tools=True,
            plan_outline_phase="refine",
        )
        is True
    )
    assert resolve_ollama_think("Product Owner", plan_outline_no_tools=False) is False


def test_resolve_ollama_think_by_role_override():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"ollamaThinkByRole": {"dev": True}})
    assert resolve_ollama_think("Developer") is True


def test_ollama_aux_chat_options_sets_think_false():
    from backend.services.llm_provider import ollama_aux_chat_options

    opts = ollama_aux_chat_options(temperature=0.2, num_predict=32)
    assert opts["think"] is False
    assert opts["temperature"] == 0.2


def test_chat_options_includes_think_in_sampling():
    from backend.agents.registry import agent_po

    initialize()
    reset_workflow_settings()
    agent_po._plan_outline_no_tools = True
    agent_po._plan_outline_phase = "write"
    save_workflow_settings({"ollamaThinkPlanOutlineWrite": True})
    opts = agent_po._chat_options()
    assert opts.get("think") is True


def test_single_model_respect_phase_routing_explore_patch():
    from backend.services.agent_efficiency import resolve_step_model

    initialize()
    reset_workflow_settings()
    ws = dict(get_workflow_settings())
    ws.update(
        {
            "singleModelMode": "on",
            "singleModelRespectPhaseRouting": True,
            "enablePhaseModelRouting": True,
            "devExploreModel": "qwen2.5-coder:7b",
            "devPatchModel": "maternion/mimo-v2.6:9b-instruct",
        }
    )
    explore, reason = resolve_step_model(
        role="Developer",
        phase="explore",
        primary_model="maternion/mimo-v2.6:9b",
        ws=ws,
    )
    assert explore == "qwen2.5-coder:7b"
    assert reason == "phase_explore"
    patch, reason2 = resolve_step_model(
        role="Developer",
        phase="patch",
        primary_model="maternion/mimo-v2.6:9b",
        ws=ws,
    )
    assert patch == "maternion/mimo-v2.6:9b-instruct"
    assert reason2 == "phase_patch"


def test_dev_phase_force_patch_after_read_batch():
    from backend.services.dev_phase_graph import DevPhaseGraph

    g = DevPhaseGraph(explore_max=8)
    action = g.record_batch(
        [("read_file", True), ("read_file", True), ("grep", True)]
    )
    assert g.forced_patch is True
    assert action.nudge is not None


def test_workflow_defaults_include_think_and_patch_after_read():
    assert DEFAULT_WORKFLOW_SETTINGS.get("ollamaThinkDefault") is False
    assert DEFAULT_WORKFLOW_SETTINGS.get("devExploreForcePatchAfterReadBatch") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("singleModelRespectPhaseRouting") is True
