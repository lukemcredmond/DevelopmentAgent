"""Optional Ollama pass to shrink bulky sprint context blocks before the agent step."""

from __future__ import annotations

from typing import Optional

from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

_COMPRESSED_HEADER = "=== COMPRESSED WORKSPACE CONTEXT ==="


_ROLE_AGENT_ATTR = {
    "Product Owner": "po",
    "Developer": "dev",
    "Code Reviewer": "cr",
    "QA Tester": "qa",
}


def _step_agent_model(agent_role: str) -> str:
    """Model already loaded for this step's agent — avoids a second model load."""
    try:
        from backend.agents.registry import AGENT_MAP, agent_dev

        agent = AGENT_MAP.get(_ROLE_AGENT_ATTR.get(agent_role, ""), agent_dev)
        return str(getattr(agent, "model", "") or "").strip()
    except Exception:
        return ""


def resolve_context_compress_model(agent_role: str = "Developer") -> str:
    ws = get_workflow_settings()
    explicit = str(ws.get("contextCompressModel") or "").strip()
    if explicit:
        return explicit
    # Prefer the step's own model: a different model here forces a VRAM swap mid-step.
    step_model = _step_agent_model(agent_role)
    if step_model:
        return step_model
    preset = str(ws.get("discordModelPresetFast") or "").strip()
    return preset or "qwen2.5-coder:7b"


def maybe_compress_sprint_context_block(
    context_block: str,
    *,
    agent_role: str = "Developer",
) -> str:
    """Return compressed block or original on skip/failure."""
    text = (context_block or "").strip()
    if not text:
        return context_block or ""
    ws = get_workflow_settings()
    if not ws.get("enableLlmContextCompress", False):
        return context_block
    min_chars = int(ws.get("contextCompressMinChars") or 8000)
    max_out = int(ws.get("contextCompressMaxChars") or 3500)
    if len(text) < min_chars:
        return context_block
    model = resolve_context_compress_model(agent_role)
    timeout = min(120.0, float(ws.get("ollamaRequestTimeoutSec") or 300))
    prompt = (
        f"Compress the following workspace context to at most {max_out} characters.\n"
        "Keep file paths, error messages, acceptance-criteria-relevant facts, and command outcomes.\n"
        "Drop boilerplate, repeated headers, and long code bodies (keep signatures only).\n"
        "Output plain text only — no markdown fences.\n\n"
        f"{text[: min(len(text), 48000)]}"
    )
    try:
        from backend.services.llm_provider import get_chat_provider

        provider = get_chat_provider()
        provider.timeout_sec = timeout
        resp = provider.chat(
            model,
            [{"role": "user", "content": prompt}],
            options={"num_predict": max(512, max_out // 2), "temperature": 0.2},
        )
        content = (resp.message.content or "").strip() if resp and resp.message else ""
        if not content:
            return context_block
        if len(content) > max_out:
            content = content[: max_out - 20] + "\n… (truncated)"
        add_system_log(
            agent_role,
            "info",
            f"Context compress {len(text)}→{len(content)} chars (model={model})",
        )
        return f"{_COMPRESSED_HEADER}\n{content}\n"
    except Exception as exc:
        add_system_log(
            agent_role,
            "info",
            f"Context compress skipped ({type(exc).__name__}); using full inject",
        )
        return context_block


def compress_header_present(text: str) -> bool:
    return _COMPRESSED_HEADER in (text or "")
