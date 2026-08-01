import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.agents.task_context import build_task_prompt, find_task_by_id, is_task_done
from backend.api.schemas import ChatPayload
from backend.services.brief_service import PO_SMALLEST_TASKS_GUIDANCE
from backend.services.logs import add_system_log
from backend.services.events import publish_event
from backend.services.project_service import save_current_project_state
from backend.workspace.files import build_file_context_block, expand_chat_mentions

router = APIRouter()

CHAT_ROLE_ADDENDUM: dict[str, str] = {
    "dev": (
        "CHAT MODE: Verify imports, packages, and dependencies by reading project manifests "
        "and source (read_file, grep, glob_file_search). Do not ask the user to confirm "
        "whether a package is imported or installed — inspect the codebase yourself."
    ),
    "cr": (
        "CHAT MODE: Use read_file and grep to verify code facts; do not ask the user to "
        "confirm imports or file contents."
    ),
    "qa": (
        "CHAT MODE: Validate with read_file and run_test/run_command; do not ask the user "
        "to confirm technical details you can inspect in the workspace."
    ),
}


def _split_intent(message: str) -> bool:
    lower = message.lower()
    keywords = (
        "split",
        "break down",
        "subtask",
        "sub-task",
        "decompose",
        "smaller task",
        "smaller card",
    )
    return any(k in lower for k in keywords)


def _compose_message(payload: ChatPayload, *, agent_role: str | None = None) -> str:
    parts: list[str] = []
    if payload.task_id:
        task = find_task_by_id(payload.task_id)
        if task:
            role = agent_role or (AGENT_MAP.get(payload.agent).role if AGENT_MAP.get(payload.agent) else None)
            parts.append(build_task_prompt(task, state.PROJECT_BRIEF, agent_role=role))
            if payload.agent == "po":
                parts.append(PO_SMALLEST_TASKS_GUIDANCE)
                parts.append(
                    "When the user asks to break down or split this card, call add_backlog_tasks "
                    "with split_from_task_id set to this task's ID yourself — never instruct the user "
                    "to call add_backlog_tasks. Each subtask needs clear acceptance criteria. "
                    "If you must reply with JSON, it must be a bare array."
                )
    addendum = CHAT_ROLE_ADDENDUM.get(payload.agent)
    if addendum:
        parts.append(addendum)
    context_block = build_file_context_block(payload.context_files)
    if context_block:
        parts.append(context_block)
    parts.append(f"User message:\n{expand_chat_mentions(payload.message)}")
    return "\n\n".join(parts)


def _refuse_done_task_if_needed(payload: ChatPayload) -> None:
    if (
        payload.task_id
        and is_task_done(payload.task_id)
        and not payload.allow_done_retry
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Task {payload.task_id} is already Done. "
                "Pass allowDoneRetry=true for a deliberate re-run."
            ),
        )


def _apply_chat_task_context(payload: ChatPayload) -> None:
    chat_task_id = payload.task_id or f"chat-{payload.agent}"
    state.ACTIVE_SPRINT_TASK_ID = chat_task_id
    state.ALLOW_DONE_RETRY = bool(payload.allow_done_retry) if payload.task_id else False
    state.STEP_FILE_READS.clear()
    agent = AGENT_MAP.get(payload.agent)
    if agent:
        state.ACTIVE_SPRINT_AGENT = agent.role


def _finalize_chat_task_context(payload: ChatPayload) -> None:
    if payload.task_id:
        save_current_project_state()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None
    state.ALLOW_DONE_RETRY = False


def _split_hint_for_response(message: str, response: str, added: int) -> str | None:
    if added > 0:
        return None
    lower_resp = response.lower()
    if _split_intent(message) or "add_backlog_tasks" in lower_resp:
        return (
            "Split didn't apply automatically — open the task and use **Split into subtasks** "
            "on the card detail (not a chat command)."
        )
    return None


def _handle_chat_simulation_fallback(payload: ChatPayload, agent) -> tuple[str, bool]:
    """Return (client_response, deferred). When deferred, assistant message is not saved yet."""
    from backend.services.simulation_gate import build_proposal, try_defer_simulation

    task_id = payload.task_id or "chat"
    prop = build_proposal(
        kind="chat",
        task_id=task_id,
        agent=agent.role,
        title=f"Chat ({payload.agent})",
        summary="Apply offline chat assistant reply",
        default_preview={"message": "(Offline simulation — Ollama unavailable.)"},
        source="chat",
        context={"agentRole": agent.role, "chatAgentId": payload.agent},
    )
    if try_defer_simulation(prop):
        return "", True
    return "(Offline simulation — Ollama unavailable.)", False


def _persist_chat_assistant(
    payload: ChatPayload,
    *,
    response: str,
    log_len_before: int,
) -> tuple[str | None, list]:
    """Save assistant message, PO split hints, board updates. Returns (split_hint, tool_calls)."""
    agent = AGENT_MAP[payload.agent]
    split_hint = None
    with state.STATE_LOCK:
        tool_calls = [dict(e) for e in state.TOOL_EXECUTION_LOG[log_len_before:]]
        if response and payload.agent == "po" and payload.task_id:
            from backend.services.sprint_service import apply_backlog_from_po_response

            added = apply_backlog_from_po_response(response, payload.task_id)
            split_hint = _split_hint_for_response(payload.message, response, added)
        elif response and payload.agent == "po" and "add_backlog_tasks" in response.lower():
            split_hint = _split_hint_for_response(payload.message, response, 0)
        _finalize_chat_task_context(payload)
        if response:
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "assistant", response, agent=agent.role
            )
            publish_event("chat", {"agent": payload.agent, "response": response[:500]})
        if payload.task_id:
            from backend.services.board_service import publish_board_update

            publish_board_update(payload.task_id, source="chat")
    return split_hint, tool_calls

@router.post("/api/chat")
def chat_with_agent(payload: ChatPayload):
    if payload.agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")
    _refuse_done_task_if_needed(payload)

    with state.STATE_LOCK:
        agent = AGENT_MAP[payload.agent]
        agent.ollama_url = payload.ollama_url
        state.storage.save_chat_message(
            state.CURRENT_PROJECT_ID, "user", payload.message, agent=agent.role
        )
        _apply_chat_task_context(payload)
        composed = _compose_message(payload, agent_role=agent.role)

    add_system_log(
        "System",
        "info",
        f"Chat start agent={payload.agent} task={payload.task_id or 'none'} "
        f"msg_len={len(payload.message)}",
    )
    split_hint = None
    tool_calls: list = []
    response = ""
    deferred_sim = False
    with state.STATE_LOCK:
        log_len_before = len(state.TOOL_EXECUTION_LOG)
    try:
        response = agent.execute_step(composed)
    finally:
        if response == "SIMULATION_FALLBACK":
            response, deferred_sim = _handle_chat_simulation_fallback(payload, agent)
        if deferred_sim:
            with state.STATE_LOCK:
                tool_calls = [dict(e) for e in state.TOOL_EXECUTION_LOG[log_len_before:]]
                _finalize_chat_task_context(payload)
        else:
            split_hint, tool_calls = _persist_chat_assistant(
                payload, response=response or "", log_len_before=log_len_before
            )

    add_system_log(
        "System",
        "info",
        f"Chat end agent={payload.agent} task={payload.task_id or 'none'} "
        f"response_len={len(response or '')} tools={len(tool_calls)}",
    )

    result = {
        "agent": payload.agent,
        "response": response,
        "messages": state.storage.get_chat_messages(state.CURRENT_PROJECT_ID),
    }
    if split_hint:
        result["splitHint"] = split_hint
    if tool_calls:
        result["toolCalls"] = tool_calls
    if deferred_sim:
        from backend.services.simulation_gate import get_pending_simulation_public

        result["pendingSimulation"] = get_pending_simulation_public()
    return result


@router.post("/api/chat/clear")
def clear_chat_history():
    with state.STATE_LOCK:
        deleted = state.storage.clear_chat_messages(state.CURRENT_PROJECT_ID)
        return {"ok": True, "deleted": deleted, "chatMessages": []}


@router.post("/api/chat/stream")
def chat_stream(payload: ChatPayload):
    if payload.agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")
    _refuse_done_task_if_needed(payload)

    def generate():
        with state.STATE_LOCK:
            agent = AGENT_MAP[payload.agent]
            agent.ollama_url = payload.ollama_url
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "user", payload.message, agent=agent.role
            )
            _apply_chat_task_context(payload)
            composed = _compose_message(payload, agent_role=agent.role)

        add_system_log(
            "System",
            "info",
            f"Chat stream start agent={payload.agent} task={payload.task_id or 'none'}",
        )

        split_hint = None
        tool_calls: list = []
        deferred_sim = False
        with state.STATE_LOCK:
            log_len_before = len(state.TOOL_EXECUTION_LOG)
        try:
            response = agent.execute_step(composed)
        finally:
            if response == "SIMULATION_FALLBACK":
                response, deferred_sim = _handle_chat_simulation_fallback(payload, agent)
            if deferred_sim:
                with state.STATE_LOCK:
                    tool_calls = [dict(e) for e in state.TOOL_EXECUTION_LOG[log_len_before:]]
                    _finalize_chat_task_context(payload)
            else:
                split_hint, tool_calls = _persist_chat_assistant(
                    payload, response=response or "", log_len_before=log_len_before
                )

        payload_out: dict = {"done": True, "response": response}
        if split_hint:
            payload_out["splitHint"] = split_hint
        if tool_calls:
            payload_out["toolCalls"] = tool_calls
        if deferred_sim:
            from backend.services.simulation_gate import get_pending_simulation_public

            payload_out["pendingSimulation"] = get_pending_simulation_public()
        yield f"data: {json.dumps(payload_out)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
