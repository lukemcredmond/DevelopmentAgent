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
                    "with split_from_task_id set to this task's ID. Each subtask needs clear "
                    "acceptance criteria. Do not only print JSON — call add_backlog_tasks. "
                    "If you must reply with JSON, it must be a bare array."
                )
    context_block = build_file_context_block(payload.context_files)
    if context_block:
        parts.append(context_block)
    parts.append(f"User message:\n{payload.message}")
    return "\n\n".join(parts)


def _apply_chat_task_context(payload: ChatPayload) -> None:
    if payload.task_id:
        state.ACTIVE_SPRINT_TASK_ID = payload.task_id
        state.STEP_FILE_READS.clear()
        agent = AGENT_MAP.get(payload.agent)
        if agent:
            state.ACTIVE_SPRINT_AGENT = agent.role


def _finalize_chat_task_context(payload: ChatPayload) -> None:
    if payload.task_id:
        save_current_project_state()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None


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

    try:
        response = agent.execute_step(composed)
    finally:
        with state.STATE_LOCK:
            if payload.agent == "po" and payload.task_id:
                from backend.services.sprint_service import apply_backlog_from_po_response

                apply_backlog_from_po_response(response, payload.task_id)
            _finalize_chat_task_context(payload)
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "assistant", response, agent=agent.role
            )
            publish_event("chat", {"agent": payload.agent, "response": response[:500]})
            if payload.task_id:
                from backend.services.board_service import publish_board_update

                publish_board_update(payload.task_id, source="chat")

    return {
        "agent": payload.agent,
        "response": response,
        "messages": state.storage.get_chat_messages(state.CURRENT_PROJECT_ID),
    }


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
            messages = [{"role": "user", "content": composed}]

        full = ""
        try:
            for chunk in agent.stream_messages(messages):
                full += chunk
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        finally:
            with state.STATE_LOCK:
                _finalize_chat_task_context(payload)
                state.storage.save_chat_message(
                    state.CURRENT_PROJECT_ID, "assistant", full, agent=agent.role
                )
                publish_event("chat", {"agent": payload.agent, "response": full[:500]})
            yield f"data: {json.dumps({'done': True, 'response': full})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
