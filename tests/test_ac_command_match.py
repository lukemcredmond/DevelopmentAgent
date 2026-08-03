"""AC checklist heuristics after run_command."""

from backend.services.ac_command_match import (
    ac_criterion_matches_command_success,
    maybe_tick_ac_for_run_command,
)
from backend.agents.task_context import init_new_task


def test_ac_matches_command_executed_phrase():
    assert ac_criterion_matches_command_success("Command executed successfully", "flutter clean")


def test_maybe_tick_ac_for_run_command():
    task = init_new_task(
        {
            "id": "T-AC",
            "title": "Clean",
            "description": "d",
            "acceptanceCriteria": [
                "Command executed successfully",
                "Build completes without errors",
            ],
        }
    )
    ticked = maybe_tick_ac_for_run_command(task, "flutter clean", success=True)
    assert 0 in ticked
    assert task["acChecklist"][0] is True
    assert task["acChecklist"][1] is False


def test_observation_hint_on_successful_run_command():
    from backend.agents.scrum_agent import ScrumAgent
    from types import SimpleNamespace

    agent = ScrumAgent(role="Developer", model="m", system_prompt="test")
    messages: list = []
    result = SimpleNamespace(
        success=True,
        duplicate_skip=False,
        tool_output="## Exit code\n0\n## Output\nclean done\n",
    )
    batch = [("run_command", {"command": "flutter clean"}, result)]
    agent._append_observation_summary(messages, batch)
    content = messages[0]["content"]
    assert "do not run it again" in content.lower() or "verification" in content.lower()
