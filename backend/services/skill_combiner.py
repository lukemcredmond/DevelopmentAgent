"""Merge multiple agent skills (workspace or library) into one project-built skill via LLM."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.registry import AGENT_LABELS, AGENT_MAP
from backend.services.logs import add_system_log
from backend.services.prompt_budget import resolve_ollama_num_ctx, skills_context_max_chars
from backend.services.skills import (
    normalize_skill_rel,
    read_skill_text,
    workspace_skill_path,
)

MAX_COMBINE_SOURCES_PER_ROUND = 5
_PARTIAL_MERGE_REL = "_partial_merge_"
_MAX_SOURCE_CHARS_EACH = 12000


def _merge_round_count(source_count: int) -> int:
    if source_count <= MAX_COMBINE_SOURCES_PER_ROUND:
        return 1
    rounds = 1
    remaining = source_count - MAX_COMBINE_SOURCES_PER_ROUND
    while remaining > 0:
        rounds += 1
        remaining -= min(MAX_COMBINE_SOURCES_PER_ROUND - 1, remaining)
    return rounds


def _merge_skills_chained(
    *,
    agent_key: str,
    sources: List[Dict[str, str]],
    ollama_url: str,
) -> tuple[str, int]:
    """Merge many skills in batches of up to 5 per LLM call."""
    n = len(sources)
    total_rounds = _merge_round_count(n)
    agent_label = _agent_role_for_key(agent_key)

    if n <= MAX_COMBINE_SOURCES_PER_ROUND:
        add_system_log(
            agent_label,
            "info",
            f"Skill combine round 1/{total_rounds} ({n} source(s))",
        )
        body = _merge_with_llm(agent_key=agent_key, sources=sources, ollama_url=ollama_url)
        return body, total_rounds

    partial = _merge_with_llm(
        agent_key=agent_key,
        sources=sources[:MAX_COMBINE_SOURCES_PER_ROUND],
        ollama_url=ollama_url,
    )
    add_system_log(
        agent_label,
        "info",
        f"Skill combine round 1/{total_rounds} ({MAX_COMBINE_SOURCES_PER_ROUND} source(s))",
    )

    idx = MAX_COMBINE_SOURCES_PER_ROUND
    round_num = 2
    chunk_size = MAX_COMBINE_SOURCES_PER_ROUND - 1
    while idx < n:
        chunk = sources[idx : idx + chunk_size]
        idx += chunk_size
        batch = [{"rel": _PARTIAL_MERGE_REL, "text": partial}] + chunk
        partial = _merge_with_llm(agent_key=agent_key, sources=batch, ollama_url=ollama_url)
        add_system_log(
            agent_label,
            "info",
            f"Skill combine round {round_num}/{total_rounds} "
            f"({len(chunk)} new source(s) + prior merge)",
        )
        round_num += 1

    return partial, total_rounds


def _slugify_output_name(name: str, *, empty_fallback: str = "combined-skill") -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-").lower()
    if not base:
        base = empty_fallback
    if not base.endswith((".md", ".txt")):
        base = f"{base}.md"
    return base


def default_built_skill_basename(agent_key: str) -> str:
    return f"{agent_key}-combined"


def _slug_stem(slug: str) -> str:
    if slug.endswith(".txt"):
        return slug[:-4]
    if slug.endswith(".md"):
        return slug[:-3]
    return slug


def _built_skill_file_exists(slug: str) -> bool:
    s = normalize_skill_rel(slug)
    rel = s if s.startswith("built/") else f"built/{s}"
    return os.path.isfile(workspace_skill_path(rel))


def resolve_built_skill_slug(
    agent_key: str,
    output_name: Optional[str] = None,
    *,
    allow_replace: bool = False,
) -> tuple[str, str, bool, str]:
    """Return (final_slug, requested_slug, requested_path_exists, suggested_basename)."""
    suggested = default_built_skill_basename(agent_key)
    if output_name and str(output_name).strip():
        requested = _slugify_output_name(output_name, empty_fallback=suggested)
    else:
        requested = _slugify_output_name(suggested, empty_fallback=suggested)

    file_exists = _built_skill_file_exists(requested)
    slug = requested

    if file_exists and not allow_replace:
        stem = _slug_stem(requested)
        ext = ".txt" if requested.endswith(".txt") else ".md"
        for i in range(2, 100):
            candidate = f"{stem}-{i}{ext}"
            if not _built_skill_file_exists(candidate):
                slug = candidate
                break

    return slug, requested, file_exists, suggested


class BuiltSkillPathExistsError(Exception):
    """Raised when saving would overwrite an existing built skill without consent."""


def _agent_role_for_key(agent_key: str) -> str:
    return AGENT_LABELS.get(agent_key, agent_key)


def _model_for_agent(agent_key: str, ollama_url: str) -> str:
    agent = AGENT_MAP.get(agent_key)
    if agent and getattr(agent, "model", None):
        if ollama_url:
            agent.ollama_url = ollama_url
        return str(agent.model)
    return "qwen2.5-coder:7b"


def _load_skill_sources(skill_files: List[str]) -> List[Dict[str, str]]:
    """Load skill bodies using workspace copy first, then global library (same as prompts)."""
    loaded: List[Dict[str, str]] = []
    for raw in skill_files:
        rel = normalize_skill_rel(raw)
        text = read_skill_text(rel)
        if text is None:
            raise FileNotFoundError(f"Skill not found in workspace or library: {rel}")
        if len(text) > _MAX_SOURCE_CHARS_EACH:
            text = text[: _MAX_SOURCE_CHARS_EACH - 40] + "\n...[source truncated for merge]\n"
        loaded.append({"rel": rel, "text": text})
    return loaded


def _merge_with_llm(
    *,
    agent_key: str,
    sources: List[Dict[str, str]],
    ollama_url: str,
) -> str:
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    timeout = min(180.0, float(ws.get("ollamaRequestTimeoutSec") or 300))
    model = _model_for_agent(agent_key, ollama_url)
    role = _agent_role_for_key(agent_key)

    blocks = []
    for s in sources:
        blocks.append(f"--- SOURCE: {s['rel']} ---\n{s['text']}\n")
    corpus = "\n".join(blocks)

    system = (
        "You merge agent skill documents into one markdown skill file. "
        "Preserve imperative rules and concrete commands. "
        "Remove duplicate and near-duplicate bullets. "
        "Do not invent new technologies, tools, or requirements. "
        "Do not drop safety or validation rules present in any source. "
        "Output ONLY the merged markdown body starting with a single # title line. "
        "Do not wrap in code fences."
    )
    user = (
        f"Target agent role: {role}\n\n"
        "Merge these skills into one coherent skill for that agent:\n\n"
        f"{corpus}"
    )

    from backend.services.llm_provider import get_chat_provider

    provider = get_chat_provider(override_url=ollama_url)
    provider.timeout_sec = timeout
    resp = provider.chat(
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"temperature": 0.2, "num_predict": 4096},
    )
    content = (resp.message.content or "").strip() if resp and resp.message else ""
    if not content:
        raise RuntimeError("Skill merge returned empty content from the model.")
    return content


def _build_frontmatter(*, agent_key: str, sources: List[str]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = ["---", "sources:"]
    for src in sources:
        lines.append(f"  - {src}")
    lines.extend([f"builtForAgent: {agent_key}", f"builtAt: {ts}", "---", ""])
    return "\n".join(lines) + "\n"


def build_combined_skill_markdown(
    *,
    agent_key: str,
    skill_files: List[str],
    body: str,
) -> str:
    rels = [normalize_skill_rel(s) for s in skill_files]
    body = body.strip()
    if body.startswith("---"):
        return body
    return _build_frontmatter(agent_key=agent_key, sources=rels) + body


def combine_skills_preview(
    *,
    agent_key: str,
    skill_files: List[str],
    output_name: Optional[str] = None,
    ollama_url: str = "http://localhost:11434",
) -> Dict[str, Any]:
    if agent_key not in AGENT_MAP:
        raise ValueError("Invalid agent")
    rels = [normalize_skill_rel(s) for s in skill_files if s and str(s).strip()]
    if len(rels) < 2:
        raise ValueError("Select at least two skills to combine")

    sources = _load_skill_sources(rels)
    merged_body, merge_rounds = _merge_skills_chained(
        agent_key=agent_key,
        sources=sources,
        ollama_url=ollama_url,
    )
    markdown = build_combined_skill_markdown(agent_key=agent_key, skill_files=rels, body=merged_body)

    slug, requested_slug, file_exists, suggested_basename = resolve_built_skill_slug(
        agent_key,
        output_name,
        allow_replace=False,
    )
    skill_rel = f"built/{slug}"
    requested_skill_rel = f"built/{requested_slug}"
    num_ctx = resolve_ollama_num_ctx(agent_key)
    budget = skills_context_max_chars(num_ctx)
    char_count = len(markdown)
    warning = None
    if char_count > budget:
        warning = (
            f"Merged skill is {char_count} chars; skills budget for this agent is ~{budget} chars. "
            "Edit before saving or assign fewer library skills."
        )

    return {
        "skillRel": skill_rel,
        "markdown": markdown,
        "charCount": char_count,
        "skillsContextMaxChars": budget,
        "sources": rels,
        "warning": warning,
        "mergeRounds": merge_rounds,
        "suggestedBasename": suggested_basename,
        "fileExists": file_exists,
        "requestedSkillRel": requested_skill_rel,
    }


def save_built_skill(
    *,
    skill_rel: str,
    markdown: str,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    rel = normalize_skill_rel(skill_rel)
    if not rel.startswith("built/"):
        raise ValueError("Built skills must live under built/")
    dest_path = workspace_skill_path(rel)
    ws_root = os.path.realpath(state.WORKSPACE_DIR)
    dest_real = os.path.realpath(dest_path)
    if not (dest_real.startswith(ws_root + os.sep) or dest_real == ws_root):
        raise ValueError("Invalid built skill path")

    if os.path.isfile(dest_path) and not replace_existing:
        raise BuiltSkillPathExistsError(
            f"Built skill already exists at '{rel}'. Check replace existing to overwrite."
        )

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    vfs_key = f"skills/{rel}".replace("\\", "/")
    state.VIRTUAL_FILESYSTEM[vfs_key] = markdown

    add_system_log(
        "System",
        "success",
        f"Saved project-built skill '{rel}' ({len(markdown)} chars).",
    )
    return {"skillRel": rel, "charCount": len(markdown)}
