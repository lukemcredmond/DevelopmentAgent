"""Step stall watchdog after synthetic read recovery."""

import time
from unittest.mock import patch

from backend.agents.scrum_agent import ScrumAgent


def test_continue_after_synthetic_read_sets_deadline():
    agent = ScrumAgent(role="Developer", model="m", system_prompt="dev")
    messages = []
    with patch("backend.services.step_diagnostics.log_event") as log_event:
        agent._continue_after_synthetic_read(
            messages,
            target="lib/main.dart",
            plan_rejection_message="retry",
        )
        log_event.assert_called_with("synthetic_read_continue", "lib/main.dart")
    assert agent._synthetic_read_deadline is not None
    assert messages[-1]["content"] == "retry"


def test_stall_deadline_expires():
    agent = ScrumAgent(role="Developer", model="m", system_prompt="dev")
    agent._synthetic_read_deadline = time.monotonic() - 1
    assert time.monotonic() > float(agent._synthetic_read_deadline)
