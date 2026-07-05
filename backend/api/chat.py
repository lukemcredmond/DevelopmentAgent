import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.api.schemas import ChatPayload
from backend.services.events import publish_event
from backend.services.project_service import save_current_project_state
from backend.workspace.files import build_file_context_block

router = APIRouter()


def _compose_message(payload: ChatPayload) -> str:
    context_block = build_file_context_block(payload.context_files)
    if context_block:
        return f"{context_block}\nUser message:\n{payload.message}"
    return payload.message


def _apply_chat_task_context(payload: ChatPayload) -> None:
    if payload.task_id:
        state.ACTIVE_SPRINT_TASK_ID = payload.task_id
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
    with state.STATE_LOCK:
        if payload.agent not in AGENT_MAP:
            raise HTTPException(status_code=400, detail="Invalid agent")
        agent = AGENT_MAP[payload.agent]
        agent.ollama_url = payload.ollama_url

        state.storage.save_chat_message(
            state.CURRENT_PROJECT_ID, "user", payload.message, agent=agent.role
        )

        _apply_chat_task_context(payload)
        try:
            response = agent.execute_step(_compose_message(payload))
        finally:
            _finalize_chat_task_context(payload)

        state.storage.save_chat_message(
            state.CURRENT_PROJECT_ID, "assistant", response, agent=agent.role
        )
        publish_event("chat", {"agent": payload.agent, "response": response[:500]})

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
            _apply_chat_task_context(payload)
            composed = _compose_message(payload)
            messages = [{"role": "user", "content": composed}]
            full = ""
            try:
                for chunk in agent.stream_messages(messages):
                    full += chunk
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            finally:
                _finalize_chat_task_context(payload)
            state.storage.save_chat_message(
                state.CURRENT_PROJECT_ID, "assistant", full, agent=agent.role
            )
            publish_event("chat", {"agent": payload.agent, "response": full[:500]})
            yield f"data: {json.dumps({'done': True, 'response': full})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
