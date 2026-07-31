"""Cross-step tool invocation fingerprints to avoid repeating identical tool+args."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.agents.tool_outcomes import summarize_tool_args

ToolKey = Tuple[str, str]

RECENT_FINGERPRINTS_MAX = 40
BLOCKED_FINGERPRINTS_MAX = 24
OVERLAP_ESCALATE_RATIO = 0.65


def tool_fingerprint_key(tool_name: str, arguments: Dict[str, Any]) -> ToolKey:
    args = arguments if isinstance(arguments, dict) else {}
    return (str(tool_name or ""), json.dumps(args, sort_keys=True, default=str))


def fingerprint_label(tool_name: str, arguments: Dict[str, Any]) -> str:
    summary = summarize_tool_args(tool_name, arguments or {})
    return f"{tool_name}({summary})"


def _entry_from_key(key: ToolKey) -> Dict[str, str]:
    tool_name, args_json = key
    try:
        args = json.loads(args_json)
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    return {
        "tool": tool_name,
        "argsJson": args_json,
        "label": fingerprint_label(tool_name, args),
    }


def _normalize_entries(raw: Any, *, cap: int) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        args_json = str(item.get("argsJson") or "")
        if not tool:
            continue
        dedupe = f"{tool}\0{args_json}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        label = str(item.get("label") or "").strip()
        if not label:
            try:
                args = json.loads(args_json)
                label = fingerprint_label(tool, args if isinstance(args, dict) else {})
            except json.JSONDecodeError:
                label = tool
        out.append({"tool": tool, "argsJson": args_json, "label": label})
    return out[-cap:]


def normalize_fingerprint_fields(task: Dict[str, Any]) -> None:
    task["recentToolFingerprints"] = _normalize_entries(
        task.get("recentToolFingerprints"), cap=RECENT_FINGERPRINTS_MAX
    )
    task["blockedToolFingerprints"] = _normalize_entries(
        task.get("blockedToolFingerprints"), cap=BLOCKED_FINGERPRINTS_MAX
    )
    for field in ("lastStepToolFingerprints", "priorStepToolFingerprints"):
        raw = task.get(field)
        if not isinstance(raw, list):
            task[field] = []
        else:
            task[field] = [str(x) for x in raw if x][-RECENT_FINGERPRINTS_MAX:]


def seed_tool_keys_from_task(task: Dict[str, Any]) -> Tuple[List[ToolKey], List[ToolKey]]:
    """Pre-load per-step duplicate counters from persisted fingerprints."""
    normalize_fingerprint_fields(task)
    success_keys: List[ToolKey] = []
    failed_keys: List[ToolKey] = []
    for entry in task.get("blockedToolFingerprints") or []:
        if isinstance(entry, dict):
            key = (str(entry.get("tool") or ""), str(entry.get("argsJson") or ""))
            if key[0]:
                success_keys.append(key)
    for entry in task.get("recentToolFingerprints") or []:
        if isinstance(entry, dict):
            key = (str(entry.get("tool") or ""), str(entry.get("argsJson") or ""))
            if key[0] and key not in success_keys:
                success_keys.append(key)
    return success_keys, failed_keys


def is_tool_fingerprint_blocked(
    task: Optional[Dict[str, Any]],
    tool_name: str,
    arguments: Dict[str, Any],
) -> bool:
    """True when this tool+args was explicitly blocked on a prior stuck step."""
    if not task:
        return False
    normalize_fingerprint_fields(task)
    key = tool_fingerprint_key(tool_name, arguments)
    for entry in task.get("blockedToolFingerprints") or []:
        if not isinstance(entry, dict):
            continue
        if (str(entry.get("tool") or ""), str(entry.get("argsJson") or "")) == key:
            return True
    return False


def record_tool_fingerprint_on_task(
    task: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
) -> None:
    normalize_fingerprint_fields(task)
    key = tool_fingerprint_key(tool_name, arguments)
    entry = _entry_from_key(key)
    recent = list(task.get("recentToolFingerprints") or [])
    recent = [e for e in recent if isinstance(e, dict) and (e.get("tool"), e.get("argsJson")) != (entry["tool"], entry["argsJson"])]
    recent.append(entry)
    task["recentToolFingerprints"] = recent[-RECENT_FINGERPRINTS_MAX:]


def block_tool_fingerprint_on_task(
    task: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
) -> None:
    normalize_fingerprint_fields(task)
    key = tool_fingerprint_key(tool_name, arguments)
    entry = _entry_from_key(key)
    blocked = list(task.get("blockedToolFingerprints") or [])
    blocked = [e for e in blocked if isinstance(e, dict) and (e.get("tool"), e.get("argsJson")) != (entry["tool"], entry["argsJson"])]
    blocked.append(entry)
    task["blockedToolFingerprints"] = blocked[-BLOCKED_FINGERPRINTS_MAX:]
    record_tool_fingerprint_on_task(task, tool_name, arguments)


def finalize_step_tool_fingerprints(
    task: Dict[str, Any],
    step_keys: List[ToolKey],
    *,
    stop_reason: str = "",
    block_keys: Optional[List[ToolKey]] = None,
) -> None:
    normalize_fingerprint_fields(task)
    labels = [fingerprint_label(k[0], json.loads(k[1]) if k[1] else {}) for k in step_keys if k[0]]
    dedup_labels: List[str] = []
    seen: Set[str] = set()
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        dedup_labels.append(label)
    task["priorStepToolFingerprints"] = list(task.get("lastStepToolFingerprints") or [])
    task["lastStepToolFingerprints"] = dedup_labels[-RECENT_FINGERPRINTS_MAX:]
    reason = str(stop_reason or "").strip().lower()
    for key in block_keys or []:
        if key[0]:
            try:
                args = json.loads(key[1]) if key[1] else {}
            except json.JSONDecodeError:
                args = {}
            block_tool_fingerprint_on_task(task, key[0], args if isinstance(args, dict) else {})
    if reason in ("duplicate_tool", "tool_failure_stop") and step_keys:
        last = step_keys[-1]
        if last[0]:
            try:
                args = json.loads(last[1]) if last[1] else {}
            except json.JSONDecodeError:
                args = {}
            block_tool_fingerprint_on_task(task, last[0], args if isinstance(args, dict) else {})


def fingerprint_overlap_ratio(a: List[str], b: List[str]) -> float:
    sa = {str(x) for x in (a or []) if x}
    sb = {str(x) for x in (b or []) if x}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(max(len(sa), len(sb)))


def should_escalate_repeat_tool_overlap(task: Dict[str, Any]) -> bool:
    normalize_fingerprint_fields(task)
    last = task.get("lastStepToolFingerprints") or []
    prior = task.get("priorStepToolFingerprints") or []
    if len(last) < 2 or len(prior) < 2:
        return False
    return fingerprint_overlap_ratio(last, prior) >= OVERLAP_ESCALATE_RATIO


def format_blocked_tools_for_prompt(task: Dict[str, Any], *, max_items: int = 8) -> str:
    normalize_fingerprint_fields(task)
    blocked = task.get("blockedToolFingerprints") or []
    if not blocked:
        return ""
    lines = []
    for entry in blocked[-max_items:]:
        if isinstance(entry, dict) and entry.get("label"):
            lines.append(f"- {entry['label']}")
    if not lines:
        return ""
    return "Do not call these again (identical args):\n" + "\n".join(lines)


def clear_fingerprint_escalation_state(task: Dict[str, Any]) -> None:
    task.pop("priorStepToolFingerprints", None)
    task.pop("lastStepToolFingerprints", None)
    task["blockedToolFingerprints"] = []
