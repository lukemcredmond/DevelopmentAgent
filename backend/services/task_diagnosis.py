"""LLM-powered task card diagnosis from transcript and metadata."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from backend.agents.registry import agent_po
from backend.agents.task_context import (
    find_task_by_id,
    get_task_lane,
    normalize_task,
    record_task_decision,
)
from backend.services.diagnostics_parser import parse_diagnostics_from_transcript
from backend.services.project_service import save_current_project_state


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n…[truncated]"


def _build_diagnosis_prompt(task: Dict[str, Any]) -> str:
    normalize_task(task)
    transcript = task.get("transcript") or []
    decisions = task.get("decisions") or []
    ac = task.get("acceptanceCriteria") or []
    qa_fail = task.get("qaFailure")

    tx_lines: List[str] = []
    for i, entry in enumerate(transcript[-50:], start=max(0, len(transcript) - 50)):
        role = entry.get("role", "?")
        tool = entry.get("toolName") or ""
        content = _truncate(str(entry.get("content") or ""), 400)
        tx_lines.append(f"[{i}] {entry.get('timestamp', '')} {role} {tool}: {content}")

    dec_lines = [
        f"- {d.get('timestamp', '')} {d.get('agent', '')} {d.get('type', '')}: {d.get('summary', '')}"
        for d in decisions[-20:]
    ]

    diagnostics = parse_diagnostics_from_transcript(transcript)
    diag_lines = [
        f"- {d.get('severity')} {d.get('file')}:{d.get('line')} {d.get('message')}"
        for d in diagnostics[:30]
    ]

    parts = [
        "Analyze this Kanban task and respond with ONLY valid JSON (no markdown fences):",
        json.dumps(
            {
                "summary": "one sentence",
                "problem": "what is wrong or stuck",
                "rootCause": "requirements|code|tests|environment|blocked",
                "evidence": ["brief evidence strings"],
                "recommendedAction": "concrete next step",
                "suggestedAgent": "po|dev|qa|user",
            },
            indent=2,
        ),
        f"\nTask ID: {task.get('id')}",
        f"Title: {task.get('title')}",
        f"Lane: {get_task_lane(str(task.get('id'))) or task.get('status')}",
        f"workType: {task.get('workType')} requiresDev: {task.get('requiresDev')} requiresQa: {task.get('requiresQa')}",
        f"Description: {_truncate(str(task.get('description') or ''), 1500)}",
        f"Acceptance criteria: {ac}",
        f"PO round trips: {task.get('poRoundTrips', 0)}",
    ]
    if qa_fail:
        parts.append(f"Last QA failure: {qa_fail}")
    if dec_lines:
        parts.append("Decisions:\n" + "\n".join(dec_lines))
    if diag_lines:
        parts.append("Parsed diagnostics:\n" + "\n".join(diag_lines))
    if tx_lines:
        parts.append("Transcript (recent):\n" + "\n".join(tx_lines))

    return _truncate("\n\n".join(parts), 12000)


def _parse_diagnosis_json(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {
                "summary": str(data.get("summary") or ""),
                "problem": str(data.get("problem") or ""),
                "rootCause": str(data.get("rootCause") or "unknown"),
                "evidence": [str(e) for e in (data.get("evidence") or [])][:10],
                "recommendedAction": str(data.get("recommendedAction") or ""),
                "suggestedAgent": str(data.get("suggestedAgent") or "dev"),
            }
    except json.JSONDecodeError:
        pass
    return {
        "summary": raw[:300],
        "problem": "Could not parse structured diagnosis",
        "rootCause": "unknown",
        "evidence": [],
        "recommendedAction": "Review transcript manually",
        "suggestedAgent": "dev",
    }


def diagnose_task(task_id: str, ollama_url: str) -> Dict[str, Any]:
    task = find_task_by_id(task_id)
    if not task:
        return {"ok": False, "error": f"Task {task_id} not found"}

    normalize_task(task)
    agent_po.ollama_url = ollama_url
    prompt = _build_diagnosis_prompt(task)
    messages = [
        {"role": "system", "content": agent_po._build_system_content()},
        {"role": "user", "content": prompt},
    ]
    response = agent_po._chat(messages, iteration=1, task_id=task_id, agent_id="po")
    raw = (response.message.content or "") if response else ""
    diagnosis = _parse_diagnosis_json(raw)
    diagnosis["taskId"] = task_id
    diagnosis["rawLength"] = len(raw or "")

    record_task_decision(
        task_id,
        "Analyst",
        "diagnosis",
        diagnosis.get("summary") or "Task diagnosis",
        detail=json.dumps(diagnosis, indent=2),
    )
    task["lastDiagnosis"] = diagnosis
    save_current_project_state()
    return {"ok": True, "diagnosis": diagnosis}
