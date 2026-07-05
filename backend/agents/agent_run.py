"""Structured agent run state for live SSE updates."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Literal, Optional
import uuid

from backend import state
from backend.services.events import publish_event

RunStatus = Literal[
    "idle",
    "thinking",
    "tool_executing",
    "awaiting_approval",
    "completed",
    "failed",
]


@dataclass
class AgentRunState:
    run_id: str
    task_id: str
    agent: str
    status: RunStatus
    current_tool: Optional[str]
    started_at: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _publish_run(run: Optional[AgentRunState]) -> None:
    if run:
        publish_event("agent_run", run.to_dict())


def start_run(task_id: str, agent: str) -> AgentRunState:
    run = AgentRunState(
        run_id=uuid.uuid4().hex[:12].upper(),
        task_id=task_id,
        agent=agent,
        status="thinking",
        current_tool=None,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    state.ACTIVE_AGENT_RUN = run
    _publish_run(run)
    return run


def update_run(
    *,
    status: Optional[RunStatus] = None,
    current_tool: Optional[str] = None,
    error: Optional[str] = None,
    clear_tool: bool = False,
) -> None:
    run = state.ACTIVE_AGENT_RUN
    if not run:
        return
    if status is not None:
        run.status = status
    if clear_tool:
        run.current_tool = None
    elif current_tool is not None:
        run.current_tool = current_tool
    if error is not None:
        run.error = error
    _publish_run(run)


def finish_run(*, status: RunStatus = "completed", error: Optional[str] = None) -> None:
    run = state.ACTIVE_AGENT_RUN
    if not run:
        return
    run.status = status
    run.current_tool = None
    if error:
        run.error = error
    _publish_run(run)
    state.ACTIVE_AGENT_RUN = None


def get_active_run() -> Optional[AgentRunState]:
    return state.ACTIVE_AGENT_RUN
