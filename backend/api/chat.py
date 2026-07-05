import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.agents.task_context import build_task_prompt, find_task_by_id
from backend.api.schemas import ChatPayload
from backend.services.brief_service import PO_SMALLEST_TASKS_GUIDANCE
from backend.services.events import publish_event
from backend.services.project_service import save_current_project_state
from backend.workspace.files import build_file_context_block

router = APIRouter()


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


def _compose_message(payload: ChatPayload) -> str:
    parts: list[str] = []
    if payload.task_id:
        task = find_task_by_id(payload.task_id)
        if task:
            parts.append(build_task_prompt(task, state.PROJECT_BRIEF))
            if payload.agent == "po":
                parts.append(PO_SMALLEST_TASKS_GUIDANCE)
                parts.append(
                    "When the user asks to break down or split this card, call add_backlog_tasks "
                    "with split_from_task_id set to this task's ID yourself — never instruct the user "
                    "to call add_backlog_tasks. Each subtask needs clear acceptance criteria. "
                    "If you must reply with JSON, it must be a bare array."
                )
    context_block = build_file_context_block(payload.context_files)
    if context_block:
        parts.append(context_block)
    parts.append(f"User message:\n{payload.message}")
    return "\n\n".join(parts)


def _apply_chat_task_context(payload: ChatPayload) -> None:
    chat_task_id = payload.task_id or f"chat-{payload.agent}"
    state.ACTIVE_SPRINT_TASK_ID = chat_task_id
    state.STEP_FILE_READS.clear()
    agent = AGENT_MAP.get(payload.agent)
    if agent:
        state.ACTIVE_SPRINT_AGENT = agent.role


def _finalize_chat_task_context(payload: ChatPayload) -> None:
    if payload.task_id:
        save_current_project_state()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None


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


@router.post("/api/chat")
def chat_with_agent(payload: ChatPayload):
    if payload.agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")

    with state.STATE_LOCK:
        agent = AGENT_MAP[payload.agent]
        agent.ollama_url = payload.ollama_url
        state.storage.save_chat_message(
            state.CURRENT_PROJECT_ID, "user", payload.message, agent=agent.role
        )
        composed = _compose_message(payload)
        _apply_chat_task_context(payload)

    split_hint = None
    tool_calls: list = []
    with state.STATE_LOCK:
        log_len_before = len(state.TOOL_EXECUTION_LOG)
    try:
        response = agent.execute_step(composed)
    finally:
        with state.STATE_LOCK:
            tool_calls = [dict(e) for e in state.TOOL_EXECUTION_LOG[log_len_before:]]
            if payload.agent == "po" and payload.task_id:
                from backend.services.sprint_service import apply_backlog_from_po_response

                added = apply_backlog_from_po_response(response, payload.task_id)
                split_hint = _split_hint_for_response(payload.message, response, added)
            elif payload.agent == "po" and "add_backlog_tasks" in response.lower():
                split_hint = _split_hint_for_response(payload.message, response, 0)
            _finalize_chat_task_context(payload)
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "assistant", response, agent=agent.role
            )
            publish_event("chat", {"agent": payload.agent, "response": response[:500]})
            if payload.task_id:
                from backend.services.board_service import publish_board_update

                publish_board_update(payload.task_id, source="chat")

    result = {
        "agent": payload.agent,
        "response": response,
        "messages": state.storage.get_chat_messages(state.CURRENT_PROJECT_ID),
    }
    if split_hint:
        result["splitHint"] = split_hint
    if tool_calls:
        result["toolCalls"] = tool_calls
    return result


@router.post("/api/chat/stream")
def chat_stream(payload: ChatPayload):
    if payload.agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")

    def generate():
        with state.STATE_LOCK:
            agent = AGENT_MAP[payload.agent]
            agent.ollama_url = payload.ollama_url
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "user", payload.message, agent=agent.role
            )
            composed = _compose_message(payload)
            _apply_chat_task_context(payload)

        split_hint = None
        tool_calls: list = []
        with state.STATE_LOCK:
            log_len_before = len(state.TOOL_EXECUTION_LOG)
        try:
            response = agent.execute_step(composed)
        finally:
            with state.STATE_LOCK:
                tool_calls = [dict(e) for e in state.TOOL_EXECUTION_LOG[log_len_before:]]
                if payload.agent == "po" and payload.task_id:
                    from backend.services.sprint_service import apply_backlog_from_po_response

                    added = apply_backlog_from_po_response(response, payload.task_id)
                    split_hint = _split_hint_for_response(payload.message, response, added)
                elif payload.agent == "po" and "add_backlog_tasks" in response.lower():
                    split_hint = _split_hint_for_response(payload.message, response, 0)
                _finalize_chat_task_context(payload)
                state.storage.save_chat_message(
                    state.CURRENT_PROJECT_ID, "assistant", response, agent=agent.role
                )
                publish_event("chat", {"agent": payload.agent, "response": response[:500]})
                if payload.task_id:
                    from backend.services.board_service import publish_board_update

                    publish_board_update(payload.task_id, source="chat")

        payload_out: dict = {"done": True, "response": response}
        if split_hint:
            payload_out["splitHint"] = split_hint
        if tool_calls:
            payload_out["toolCalls"] = tool_calls
        yield f"data: {json.dumps(payload_out)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
