"""Apply Product Owner clarification JSON and leave Needs PO without extra LLM turns."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.agents.task_context import (
    apply_po_clarification,
    find_task_by_id,
    get_task_lane,
)
from backend.services.board_service import move_board_stage
from backend.services.brief_service import append_brief_text, record_brief_changelog
from backend.services.logs import add_system_log

_PO_ADVANCE_LANES = frozenset({"In Progress", "Refinement"})

# Default PO decode cap; bump once when Gemma hits the wall with no tool/JSON.
PO_NUM_PREDICT_DEFAULT = 2048
PO_NUM_PREDICT_BUMP = 4096
PO_TRUNCATED_RETRY_MESSAGE = (
    "Your previous reply was cut off at the token cap before any update_board call "
    "or clarification JSON. Reply now with a short JSON object "
    '{"description": "...", "acceptanceCriteria": ["..."]} '
    "and call update_board to In Progress. Do not write a long essay."
)
PO_TRUNCATED_STOP = (
    "Stopped: PO generation truncated without clarification JSON or update_board."
)
PO_INCOMPLETE_STOP = (
    "Stopped: PO clarification incomplete — no JSON or update_board."
)


def extract_json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from fenced blocks, whole text, or the first balanced braces."""
    raw = str(text or "")
    if not raw.strip():
        return None
    bt = "```"
    json_blocks = re.findall(rf"{bt}json\s*(.*?)\s*{bt}", raw, re.DOTALL | re.IGNORECASE)
    for block in json_blocks:
        parsed = _loads_dict(block.strip())
        if parsed is not None:
            return parsed
    stripped = raw.strip()
    parsed = _loads_dict(stripped)
    if parsed is not None:
        return parsed
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(stripped[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                parsed = _loads_dict(stripped[start : i + 1])
                if parsed is not None:
                    return parsed
                break
    return None


def _loads_dict(blob: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def clarification_fields_from_mapping(obj: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[List[str]], str]:
    if not isinstance(obj, dict):
        return None, None, ""
    description = obj.get("description")
    description = str(description).strip() if description else None
    ac_raw = obj.get("acceptanceCriteria") or obj.get("acceptance_criteria")
    ac: Optional[List[str]] = None
    if isinstance(ac_raw, list):
        ac = [str(c).strip() for c in ac_raw if str(c).strip()]
    elif isinstance(ac_raw, str) and ac_raw.strip():
        ac = [ac_raw.strip()]
    addition = obj.get("briefAddition") or obj.get("brief_addition") or ""
    addition = str(addition).strip()
    return description or None, ac, addition


def clarification_fingerprint(description: Optional[str], acceptance_criteria: Optional[List[str]]) -> str:
    ac = "|".join(acceptance_criteria or [])
    raw = f"{(description or '').strip()}\n{ac}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def record_clarification_fingerprint(task: Dict[str, Any], fingerprint: str) -> int:
    """Track identical PO JSON across steps. Returns consecutive identical count."""
    if not isinstance(task, dict) or not fingerprint:
        return 0
    prev = str(task.get("lastPoClarificationFingerprint") or "")
    if prev == fingerprint:
        count = int(task.get("identicalPoClarificationCount") or 0) + 1
    else:
        count = 1
        task["lastPoClarificationFingerprint"] = fingerprint
    task["identicalPoClarificationCount"] = count
    return count


def apply_clarification_payload(
    task_id: str,
    *,
    description: Optional[str] = None,
    acceptance_criteria: Optional[List[str]] = None,
    brief_addition: str = "",
) -> bool:
    if not description and not acceptance_criteria:
        return False
    apply_po_clarification(
        task_id,
        description=description,
        acceptance_criteria=acceptance_criteria,
    )
    if brief_addition:
        append_brief_text(brief_addition, "po", f"PO clarification for {task_id}")
    task = find_task_by_id(task_id)
    if task:
        fp = clarification_fingerprint(description, acceptance_criteria)
        record_clarification_fingerprint(task, fp)
        record_brief_changelog("po", f"Clarified {task.get('title') or task_id}", (description or "")[:300])
    return True


def apply_clarification_from_text(task_id: str, text: str) -> bool:
    obj = extract_json_object_from_text(text)
    description, ac, addition = clarification_fields_from_mapping(obj)
    return apply_clarification_payload(
        task_id,
        description=description,
        acceptance_criteria=ac,
        brief_addition=addition,
    )


def apply_clarification_from_board_args(task_id: str, arguments: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(arguments, dict):
        return False
    description, ac, addition = clarification_fields_from_mapping(arguments)
    return apply_clarification_payload(
        task_id,
        description=description,
        acceptance_criteria=ac,
        brief_addition=addition,
    )


def _ensure_dev_claim_fields(task: Dict[str, Any]) -> None:
    """Fill spec gaps so Needs PO → In Progress is not bounced by the Dev claim gate."""
    desc = str(task.get("description") or "").strip()
    ac = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if desc and not str(task.get("scope") or "").strip():
        task["scope"] = desc
    if not str(task.get("testPlan") or "").strip() and ac:
        task["testPlan"] = "; ".join(ac[:5])
    if not str(task.get("userStory") or "").strip() and desc:
        task["userStory"] = desc


def move_off_needs_po(task_id: str) -> Optional[str]:
    """Move Needs PO → In Progress (or Refinement). Returns destination lane or None."""
    lane = get_task_lane(task_id) or ""
    if lane != "Needs PO":
        return lane or None
    task = find_task_by_id(task_id)
    if not task:
        return None
    _ensure_dev_claim_fields(task)
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    if (
        ws.get("requireBacklogRefinement")
        and int(task.get("refinementRoundTrips") or 0) > 0
        and not task.get("refinementComplete")
    ):
        dest = "Refinement"
        task["refinementStatus"] = "po_updated"
    else:
        dest = "In Progress"
    move_board_stage(task_id, dest)
    return dest


def complete_needs_po_clarification(
    task_id: str,
    *,
    text: Optional[str] = None,
    board_args: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Persist JSON (from text and/or update_board args) and leave Needs PO when possible."""
    applied = False
    if board_args:
        applied = apply_clarification_from_board_args(task_id, board_args) or applied
    if text:
        applied = apply_clarification_from_text(task_id, text) or applied
    lane = get_task_lane(task_id) or ""
    if lane in _PO_ADVANCE_LANES:
        return True, f"Task {task_id} is already in '{lane}'."
    if lane != "Needs PO":
        return applied, f"Task {task_id} is in '{lane}'."
    task = find_task_by_id(task_id)
    identical = int((task or {}).get("identicalPoClarificationCount") or 0)
    should_move = applied or identical >= 1
    if not should_move:
        return False, "Clarification JSON missing — card stays in Needs PO."
    dest = move_off_needs_po(task_id)
    if dest and dest != "Needs PO":
        add_system_log(
            "Product Owner",
            "success",
            f"PO clarification applied — moved {task_id} to {dest}",
        )
        return True, f"PO clarification applied and task moved to '{dest}'."
    return applied, "Clarification applied but card stayed in Needs PO."


def is_po_needs_po_advance(tool_name: str, arguments: Optional[Dict[str, Any]]) -> bool:
    from backend import state

    if tool_name != "update_board":
        return False
    if str(getattr(state, "ACTIVE_SPRINT_AGENT", "") or "") != "Product Owner":
        return False
    args = arguments if isinstance(arguments, dict) else {}
    target = str(args.get("target_lane") or "").strip()
    if target not in _PO_ADVANCE_LANES:
        return False
    task_id = str(args.get("task_id") or getattr(state, "ACTIVE_SPRINT_TASK_ID", "") or "")
    if not task_id:
        return False
    return (get_task_lane(task_id) or "") == "Needs PO"


def finish_po_board_noop(task_id: str, arguments: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Apply clarification if present and end the step after a skipped update_board."""
    ok, msg = complete_needs_po_clarification(task_id, board_args=arguments)
    if ok:
        return True, msg
    lane = get_task_lane(task_id) or ""
    if lane in _PO_ADVANCE_LANES:
        return True, f"Task {task_id} is already in '{lane}' — do not restate clarification JSON."
    return False, (
        f"update_board skipped. Do not restate the clarification JSON. "
        f"Card is in '{lane or 'Needs PO'}'."
    )


def po_turn_hit_generation_cap(
    *,
    eval_tokens: int,
    num_predict: Optional[int],
    tool_names: Sequence[str],
    content: str,
) -> bool:
    """True when decode hit num_predict and produced neither tools nor parseable clarification."""
    if tool_names:
        return False
    cap = int(num_predict or 0)
    if cap <= 0:
        return False
    eval_n = int(eval_tokens or 0)
    if eval_n < max(1, cap - 2):
        return False
    obj = extract_json_object_from_text(content or "")
    description, ac, _ = clarification_fields_from_mapping(obj)
    return not description and not ac


def prune_repeated_po_json(content: str, *, max_chars: int = 200) -> str:
    """Collapse already-parsed clarification JSON in prompt slices."""
    text = str(content or "")
    obj = extract_json_object_from_text(text)
    description, ac, _ = clarification_fields_from_mapping(obj)
    if description or ac:
        return "[clarification JSON already recorded — do not repeat]"
    return text[:max_chars]
