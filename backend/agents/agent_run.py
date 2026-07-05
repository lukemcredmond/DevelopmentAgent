"""Structured agent run state for live SSE updates."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
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

MAX_RECENT_TOOLS = 5


@dataclass
class AgentRunState:
    run_id: str
    task_id: str
    agent: str
    status: RunStatus
    current_tool: Optional[str]
    started_at: str
    error: Optional[str] = None
    iteration: int = 0
    max_iterations: int = 8
    recent_tools: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _publish_run(run: Optional[AgentRunState]) -> None:
    if run:
        publish_event("agent_run", run.to_dict())


def start_run(task_id: str, agent: str, *, max_iterations: int = 8) -> AgentRunState:
    run = AgentRunState(
        run_id=uuid.uuid4().hex[:12].upper(),
        task_id=task_id,
        agent=agent,
        status="thinking",
        current_tool=None,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        iteration=0,
        max_iterations=max_iterations,
        recent_tools=[],
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
    iteration: Optional[int] = None,
    max_iterations: Optional[int] = None,
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
    if iteration is not None:
        run.iteration = iteration
    if max_iterations is not None:
        run.max_iterations = max_iterations
    _publish_run(run)


def append_recent_tool(entry: Dict[str, Any]) -> None:
    run = state.ACTIVE_AGENT_RUN
    if not run:
        return
    run.recent_tools = (run.recent_tools + [entry])[-MAX_RECENT_TOOLS:]
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
