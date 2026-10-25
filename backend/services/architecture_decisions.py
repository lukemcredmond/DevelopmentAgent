"""Per-card architecture decision records (ADR) with supersession chains."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

MAX_ARCHITECTURE_DECISIONS = 50
ADR_ID_PREFIX = "ADR-"

_TOPIC_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_adr_topic(topic: str) -> str:
    raw = str(topic or "").strip().lower()
    slug = _TOPIC_SLUG_RE.sub("-", raw).strip("-")
    return slug[:80] or "general"


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_adr_id() -> str:
    return f"{ADR_ID_PREFIX}{uuid.uuid4().hex.upper()}"


def get_architecture_decisions(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = task.get("architectureDecisions")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict)]


def _normalize_alternatives(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
            rejected = str(
                item.get("rejectedBecause")
                or item.get("rejected_because")
                or item.get("reason")
                or ""
            ).strip()
            if name:
                out.append({"name": name[:200], "rejectedBecause": rejected[:1000]})
        elif isinstance(item, str) and item.strip():
            out.append({"name": item.strip()[:200], "rejectedBecause": ""})
    return out[:12]


def _find_adr_by_id(task: Dict[str, Any], adr_id: str) -> Optional[Dict[str, Any]]:
    for d in get_architecture_decisions(task):
        if str(d.get("id") or "") == adr_id:
            return d
    return None


def _find_accepted_by_topic(task: Dict[str, Any], topic: str) -> Optional[Dict[str, Any]]:
    key = normalize_adr_topic(topic)
    for d in reversed(get_architecture_decisions(task)):
        if d.get("status") != "accepted":
            continue
        if normalize_adr_topic(str(d.get("topic") or "")) == key:
            return d
    return None


def validate_adr_payload(payload: Dict[str, Any], *, require_change_note_on_supersede: bool = True) -> Tuple[bool, str]:
    decision = str(payload.get("decision") or "").strip()
    if not decision:
        return False, "decision is required"
    alts = _normalize_alternatives(payload.get("alternatives"))
    if not alts:
        return False, "at least one alternative is required"
    topic = normalize_adr_topic(str(payload.get("topic") or payload.get("title") or ""))
    if not topic:
        return False, "topic is required"
    if require_change_note_on_supersede and payload.get("supersedesId"):
        if not str(payload.get("changeNote") or payload.get("change_note") or "").strip():
            return False, "changeNote is required when superseding a prior ADR"
    return True, ""


def append_architecture_decision(
    task_id: str,
    payload: Dict[str, Any],
    *,
    author: str = "User",
    status: str = "accepted",
) -> Tuple[Optional[Dict[str, Any]], str]:
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(task_id)
    if not task:
        return None, f"Task not found: {task_id}"
    normalize_task(task)
    if "architectureDecisions" not in task or not isinstance(task["architectureDecisions"], list):
        task["architectureDecisions"] = []

    ok, err = validate_adr_payload(payload)
    if not ok:
        return None, err

    topic = normalize_adr_topic(str(payload.get("topic") or payload.get("title") or ""))
    title = str(payload.get("title") or topic).strip()[:300]
    context = str(payload.get("context") or "").strip()[:4000]
    decision = str(payload.get("decision") or "").strip()[:4000]
    consequences = str(payload.get("consequences") or "").strip()[:2000]
    change_note = str(payload.get("changeNote") or payload.get("change_note") or "").strip()[:2000]
    supersedes_id = str(payload.get("supersedesId") or payload.get("supersedes_id") or "").strip()

    related_raw = payload.get("relatedFiles") or payload.get("related_files") or []
    related_files: List[str] = []
    if isinstance(related_raw, list):
        related_files = [str(p).strip() for p in related_raw if str(p).strip()][:24]
    elif isinstance(related_raw, str) and related_raw.strip():
        related_files = [related_raw.strip()]

    prior = None
    if supersedes_id:
        prior = _find_adr_by_id(task, supersedes_id)
    if not prior:
        prior = _find_accepted_by_topic(task, topic)
        if prior and not change_note:
            change_note = str(payload.get("rationale") or "Updated approach for this topic.").strip()[:2000]
            supersedes_id = str(prior.get("id") or "")

    if prior and prior.get("status") == "accepted":
        prior["status"] = "superseded"
        prior["updatedAt"] = _now_ts()
        if change_note:
            prior["supersededByChangeNote"] = change_note[:2000]

    ts = _now_ts()
    record: Dict[str, Any] = {
        "id": _new_adr_id(),
        "topic": topic,
        "title": title,
        "status": status if status in ("accepted", "proposed", "superseded") else "accepted",
        "createdAt": ts,
        "updatedAt": ts,
        "author": str(author or "User")[:80],
        "context": context,
        "decision": decision,
        "alternatives": _normalize_alternatives(payload.get("alternatives")),
        "consequences": consequences,
        "relatedFiles": related_files,
    }
    if supersedes_id:
        record["supersedesId"] = supersedes_id
    if change_note:
        record["changeNote"] = change_note

    task["architectureDecisions"].append(record)
    if len(task["architectureDecisions"]) > MAX_ARCHITECTURE_DECISIONS:
        task["architectureDecisions"] = task["architectureDecisions"][-MAX_ARCHITECTURE_DECISIONS:]

    sync_task_architecture_docs(task_id)
    try:
        from backend.services.project_service import save_current_project_state

        save_current_project_state()
    except Exception:
        pass
    return record, ""


def format_architecture_decisions_for_prompt(
    task: Dict[str, Any], *, max_accepted: int = 8, max_superseded: int = 4
) -> str:
    adrs = get_architecture_decisions(task)
    if not adrs:
        return ""
    accepted = [d for d in adrs if d.get("status") == "accepted"]
    superseded = [d for d in adrs if d.get("status") == "superseded"]
    lines = ["\n=== ARCHITECTURE DECISIONS (design rationale — honor unless superseded) ===\n"]
    for d in accepted[-max_accepted:]:
        lines.append(f"**{d.get('title', d.get('topic', '?'))}** [{d.get('topic', '')}]")
        if d.get("context"):
            lines.append(f"  Context: {str(d['context'])[:400]}")
        lines.append(f"  Decision: {str(d.get('decision', ''))[:500]}")
        alts = d.get("alternatives") or []
        if alts:
            alt_parts = []
            for a in alts[:4]:
                if isinstance(a, dict):
                    alt_parts.append(
                        f"{a.get('name', '?')} (rejected: {str(a.get('rejectedBecause', ''))[:120]})"
                    )
            if alt_parts:
                lines.append(f"  Alternatives considered: {'; '.join(alt_parts)}")
        if d.get("consequences"):
            lines.append(f"  Consequences: {str(d['consequences'])[:300]}")
        lines.append("")
    if superseded:
        lines.append("Recently superseded (do not reintroduce without a new ADR):")
        for d in superseded[-max_superseded:]:
            note = d.get("supersededByChangeNote") or d.get("changeNote") or ""
            lines.append(
                f"- {d.get('title', d.get('topic', '?'))}: was '{str(d.get('decision', ''))[:120]}'"
                + (f" — {note[:160]}" if note else "")
            )
        lines.append("")
    return "\n".join(lines)


def _parse_extract_json(raw: str) -> List[Dict[str, Any]]:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("decisions") or data.get("architectureDecisions") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    return [x for x in items if isinstance(x, dict)]


def _step_transcript_snippet(task: Dict[str, Any], limit: int = 12) -> str:
    lines: List[str] = []
    for entry in (task.get("transcript") or [])[-40:]:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "tool":
            continue
        tool = str(entry.get("toolName") or "")
        if tool not in (
            "read_file",
            "write_file",
            "apply_patch",
            "run_command",
            "record_architecture_decision",
        ):
            continue
        ok = entry.get("toolSuccess")
        content = str(entry.get("content") or entry.get("toolOutput") or "")[:280]
        lines.append(f"- {tool} ({'ok' if ok else 'fail'}): {content}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _files_touched_recent(task: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    seen: set[str] = set()
    for entry in (task.get("files") or [])[-20:]:
        if isinstance(entry, dict):
            p = str(entry.get("path") or "").strip()
        else:
            p = str(entry).strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths[:16]


def adr_post_step_extract_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    from backend.services.workflow_settings import get_workflow_settings

    settings = ws or get_workflow_settings()
    if settings.get("enableAdrPostStepExtract") is False:
        return False
    return bool(settings.get("enableAdrPostStepExtract", True))


def maybe_extract_architecture_decisions(
    task_id: str,
    *,
    exit_reason: Optional[str] = None,
    ollama_url: Optional[str] = None,
) -> int:
    """After a Dev step with writes, propose 0–N ADRs via one JSON-only LLM call."""
    from backend import state
    from backend.agents.registry import agent_dev
    from backend.agents.task_context import find_task_by_id, normalize_task
    from backend.services.sprint_service import _task_has_write_files
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    if not adr_post_step_extract_enabled(ws):
        return 0
    max_per_step = max(0, min(2, int(ws.get("adrPostStepExtractMaxPerStep") or 1)))
    if max_per_step <= 0:
        return 0

    task = find_task_by_id(task_id)
    if not task:
        return 0
    normalize_task(task)
    if not _task_has_write_files(task):
        return 0
    if exit_reason and exit_reason in ("SIMULATION_FALLBACK", "ollama_fallback", "llm_call_failed"):
        return 0

    url = ollama_url or str(getattr(agent_dev, "ollama_url", "") or "http://localhost:11434")
    agent_dev.ollama_url = url

    ac = task.get("acceptanceCriteria") or []
    ac_lines = [str(c) for c in ac[:6]]
    existing = get_architecture_decisions(task)
    existing_topics = [
        f"{d.get('topic', '')}: {d.get('title', '')}" for d in existing if d.get("status") == "accepted"
    ][-10:]

    prompt = (
        "Extract architecture decision record(s) from this Developer step.\n"
        "Reply with ONLY JSON: {\"decisions\": [{\"topic\",\"title\",\"context\",\"decision\","
        "\"alternatives\":[{\"name\",\"rejectedBecause\"}],\"consequences\",\"changeNote\"}]}\n"
        f"Max {max_per_step} decision(s). Skip trivial file edits. Each decision MUST include "
        "at least one rejected alternative with rejectedBecause.\n"
        f"If updating an existing topic, set changeNote explaining the shift.\n"
        f"Existing accepted topics: {existing_topics or '(none)'}\n"
        f"Title: {task.get('title', '')}\n"
        f"Description: {str(task.get('description', ''))[:800]}\n"
        f"AC: {ac_lines}\n"
        f"Files touched: {_files_touched_recent(task)}\n"
        f"Recent tools:\n{_step_transcript_snippet(task)}\n"
    )
    messages = [
        {"role": "system", "content": "You output strict JSON only. No markdown prose."},
        {"role": "user", "content": prompt},
    ]
    try:
        response = agent_dev._chat(messages, iteration=1, task_id=task_id, agent_id="dev")
        raw = (response.message.content or "") if response else ""
    except Exception:
        return 0

    added = 0
    for item in _parse_extract_json(raw)[:max_per_step]:
        record, err = append_architecture_decision(
            task_id, item, author="Developer (auto-extract)"
        )
        if record and not err:
            added += 1
    if added:
        from backend.services.logs import add_system_log

        add_system_log(
            "System",
            "info",
            f"{task_id}: recorded {added} architecture decision(s) from post-step extract",
        )
    return added


def tool_record_architecture_decision(
    task_id: str,
    topic: str,
    title: str,
    context: str,
    decision: str,
    alternatives: Any,
    consequences: str = "",
    related_files: Any = None,
    supersedes_id: str = "",
    change_note: str = "",
    **_extra: Any,
) -> str:
    from backend import state

    author = str(state.ACTIVE_SPRINT_AGENT or "Agent")
    payload: Dict[str, Any] = {
        "topic": topic,
        "title": title,
        "context": context,
        "decision": decision,
        "alternatives": alternatives,
        "consequences": consequences,
        "related_files": related_files,
        "supersedes_id": supersedes_id,
        "change_note": change_note,
    }
    record, err = append_architecture_decision(task_id, payload, author=author)
    if err:
        return f"Error: {err}"
    return f"Recorded architecture decision {record.get('id')} — {record.get('title', topic)}"


# Markdown sync (imported by task_architecture_markdown pattern)
def sync_task_architecture_docs(task_id: str) -> None:
    from backend.services.task_architecture_markdown import update_task_architecture_markdown

    try:
        update_task_architecture_markdown(str(task_id))
    except Exception:
        pass
