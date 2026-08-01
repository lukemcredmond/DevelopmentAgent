"""LLM-driven Tool Health probes — ask the model to call a tool once, then execute."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from backend.services.tool_probe import build_model_hints, should_skip_probe


from backend.services.llm_tool_recovery import normalize_tool_arguments


def _resolve_agent_model(agent_id: str, model: Optional[str] = None) -> tuple[Any, str]:
    from backend.agents.registry import AGENT_MAP

    agent = AGENT_MAP.get(agent_id)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_id}")
    resolved = (model or "").strip() or str(getattr(agent, "model", "") or "")
    if not resolved:
        raise ValueError(f"No model configured for agent {agent_id}")
    return agent, resolved


def _single_tool_ollama_schema(agent: Any, tool_name: str) -> Optional[Dict[str, Any]]:
    for schema in agent.registry.get_ollama_tools():
        fn = (schema.get("function") or {}) if isinstance(schema, dict) else {}
        if fn.get("name") == tool_name:
            return schema
    return None


def _extract_first_tool_call(message: Any) -> tuple[Optional[str], Dict[str, Any]]:
    if message is None:
        return None, {}
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls") or []
    else:
        tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return None, {}
    tc = tool_calls[0]
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        name = fn.get("name") if isinstance(fn, dict) else None
        args = fn.get("arguments") if isinstance(fn, dict) else {}
        return (str(name) if name else None), _normalize_tool_arguments(args)
    fn = getattr(tc, "function", None)
    name = getattr(fn, "name", None) if fn is not None else None
    args = getattr(fn, "arguments", None) if fn is not None else None
    return (str(name) if name else None), _normalize_tool_arguments(args)


def run_llm_tool_probe(
    agent_id: str,
    tool_name: str,
    *,
    model: Optional[str] = None,
    include_destructive: bool = False,
    chat_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    One Ollama chat turn with only ``tool_name`` available.
    Pass if the model calls that tool and execute_tool succeeds.
    ``chat_fn`` is for tests: (model, messages, tools, options) -> response-like object.
    """
    from backend.agents.registry import configure_agent_tools
    from backend.services.tool_execution_service import execute_tool, list_agent_tools
    from backend.services.workflow_settings import get_workflow_settings

    configure_agent_tools()
    agent, resolved_model = _resolve_agent_model(agent_id, model)

    parameters: Dict[str, Any] = {}
    for defn in list_agent_tools(agent_id):
        if defn.get("name") == tool_name:
            parameters = defn.get("parameters") or {}
            break

    skip_reason = should_skip_probe(tool_name, include_destructive=include_destructive)
    if skip_reason:
        hints = build_model_hints(tool_name, parameters, status="skip")
        return {
            "toolName": tool_name,
            "status": "skip",
            "success": False,
            "output": f"Skipped: {skip_reason}",
            "durationMs": 0,
            "hints": hints,
            "probeArgs": {},
            "skipReason": skip_reason,
            "mode": "llm",
            "model": resolved_model,
            "modelCalledTool": False,
            "llmContent": "",
        }

    schema = _single_tool_ollama_schema(agent, tool_name)
    if schema is None:
        hints = build_model_hints(tool_name, parameters, status="fail")
        hints.append("Tool is not registered on this agent's allowlist.")
        return {
            "toolName": tool_name,
            "status": "fail",
            "success": False,
            "output": f"Tool '{tool_name}' is not registered for agent '{agent_id}'",
            "durationMs": 0,
            "hints": hints,
            "probeArgs": {},
            "skipReason": None,
            "mode": "llm",
            "model": resolved_model,
            "modelCalledTool": False,
            "llmContent": "",
        }

    hints_for_prompt = build_model_hints(tool_name, parameters, status="untested")
    hint_block = "\n".join(f"- {h}" for h in hints_for_prompt) if hints_for_prompt else "- Use valid arguments."
    system = (
        "You are verifying tool calling. You must call the provided tool exactly once. "
        "Do not answer with prose only. Prefer a tool call over text."
    )
    user = (
        f"Call the tool `{tool_name}` exactly once with valid arguments for a safe smoke check.\n"
        f"Hints:\n{hint_block}\n"
        "Return a tool call only."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    tools = [schema]
    ws = get_workflow_settings()
    options = {
        "temperature": 0.1,
        "num_ctx": int(ws.get("ollamaNumCtx", 32768)),
    }

    started = time.time()
    llm_content = ""
    try:
        if chat_fn is not None:
            result = chat_fn(resolved_model, messages, tools, options)
        else:
            client = agent._get_client()
            result = client.chat(
                model=resolved_model,
                messages=messages,
                tools=tools,
                stream=False,
                options=options,
            )
        duration_ms = int((time.time() - started) * 1000)
        message = getattr(result, "message", None)
        if message is None and isinstance(result, dict):
            message = result.get("message")
        if isinstance(message, dict):
            llm_content = str(message.get("content") or "")[:2000]
        else:
            llm_content = str(getattr(message, "content", None) or "")[:2000]
        called_name, called_args = _extract_first_tool_call(message)
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        output = f"Ollama error: {exc}"
        hints = build_model_hints(tool_name, parameters, status="fail", output=output)
        return {
            "toolName": tool_name,
            "status": "fail",
            "success": False,
            "output": output[:4000],
            "durationMs": duration_ms,
            "hints": hints,
            "probeArgs": {},
            "skipReason": None,
            "mode": "llm",
            "model": resolved_model,
            "modelCalledTool": False,
            "llmContent": "",
        }

    if not called_name:
        output = "Model did not call a tool." + (f" Content: {llm_content}" if llm_content else "")
        hints = build_model_hints(tool_name, parameters, status="fail", output=output)
        hints.append("The model must emit a native tool call for this tool (not JSON in text).")
        return {
            "toolName": tool_name,
            "status": "fail",
            "success": False,
            "output": output[:4000],
            "durationMs": duration_ms,
            "hints": hints,
            "probeArgs": {},
            "skipReason": None,
            "mode": "llm",
            "model": resolved_model,
            "modelCalledTool": False,
            "llmContent": llm_content,
        }

    if called_name != tool_name:
        output = f"Model called '{called_name}' instead of '{tool_name}'."
        hints = build_model_hints(tool_name, parameters, status="fail", output=output)
        return {
            "toolName": tool_name,
            "status": "fail",
            "success": False,
            "output": output,
            "durationMs": duration_ms,
            "hints": hints,
            "probeArgs": called_args,
            "skipReason": None,
            "mode": "llm",
            "model": resolved_model,
            "modelCalledTool": True,
            "llmContent": llm_content,
        }

    exec_started = time.time()
    result = execute_tool(
        agent_id,
        tool_name,
        called_args,
        task_id=None,
        source="manual",
        skip_approval=True,
        user_prompt=f"Tool Health LLM probe: {tool_name}",
    )
    exec_ms = int((time.time() - exec_started) * 1000)
    success = bool(result.success)
    status = "pass" if success else "fail"
    output = (result.tool_output or "")[:4000]
    hints = build_model_hints(tool_name, parameters, status=status, output=output)
    return {
        "toolName": tool_name,
        "status": status,
        "success": success,
        "output": output,
        "durationMs": duration_ms + exec_ms,
        "hints": hints,
        "probeArgs": called_args,
        "skipReason": None,
        "mode": "llm",
        "model": resolved_model,
        "modelCalledTool": True,
        "llmContent": llm_content,
    }


def run_llm_probe_all(
    agent_id: str,
    *,
    model: Optional[str] = None,
    include_destructive: bool = False,
    chat_fn: Optional[Any] = None,
    delay_sec: float = 0.15,
) -> List[Dict[str, Any]]:
    from backend.services.tool_execution_service import list_agent_tools

    results: List[Dict[str, Any]] = []
    tools = list_agent_tools(agent_id)
    for i, defn in enumerate(tools):
        name = str(defn.get("name") or "")
        if not name:
            continue
        if i > 0 and delay_sec > 0:
            time.sleep(delay_sec)
        results.append(
            run_llm_tool_probe(
                agent_id,
                name,
                model=model,
                include_destructive=include_destructive,
                chat_fn=chat_fn,
            )
        )
    return results
