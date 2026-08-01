"""Defer offline SIMULATION_FALLBACK until user confirms (or supplies override)."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import state
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

VALID_OVERRIDE_TARGETS = frozenset(
    {
        "agent_text",
        "dev_file_content",
        "board_lane",
        "qa_pass",
        "qa_fail",
        "po_output",
        "skip_step",
    }
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_proposal_id() -> str:
    return f"sim-{uuid.uuid4().hex[:12]}"


def get_pending_simulation_public() -> Optional[Dict[str, Any]]:
    raw = getattr(state, "PENDING_SIMULATION", None)
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return {
        "id": raw.get("id"),
        "taskId": raw.get("taskId"),
        "agent": raw.get("agent"),
        "kind": raw.get("kind"),
        "title": raw.get("title"),
        "summary": raw.get("summary"),
        "defaultPreview": raw.get("defaultPreview") or {},
        "createdAt": raw.get("createdAt"),
        "source": raw.get("source"),
    }


def _dev_offline_file(title: str) -> tuple[str, str]:
    lower = title.lower()
    if "meal" in lower or "recipe" in lower or "api" in lower:
        return (
            "meal_service.js",
            "module.exports = function fetchMealsQuery(q) { return { success: true, meals: [] }; };",
        )
    if "auth" in lower or "secure" in lower:
        return "auth.js", "module.exports = function authenticateUser(u) { return 'token'; };"
    return "index.js", "function init() { console.log('init'); }\ninit();"


def preview_sprint_dev(task: Dict[str, Any]) -> Dict[str, Any]:
    file_name, content = _dev_offline_file(str(task.get("title") or ""))
    from backend.services.sprint_service import _dev_complete_lane

    return {
        "fileName": file_name,
        "fileContent": content[:500],
        "targetLane": _dev_complete_lane(),
    }


def preview_sprint_cr() -> Dict[str, Any]:
    return {"likelyOutcome": "QA (80%) or In Progress (20%)", "note": "Random offline review"}


def preview_sprint_qa() -> Dict[str, Any]:
    return {"likelyOutcome": "Done (85%) or In Progress with QA fail (15%)", "note": "Random offline QA"}


def preview_po_plan_outline() -> Dict[str, Any]:
    return {
        "outlineSnippet": "## Summary\nOffline plan stub.\n…",
    }


def preview_po_backlog_stub() -> Dict[str, Any]:
    return {"note": "Creates 2 epics with sample child cards (offline)"}


def mark_step_outcome_simulation_pending(task_id: str, agent: str, lane_before: str) -> None:
    from backend.agents.task_context import get_task_lane

    lane_after = get_task_lane(task_id) or lane_before
    state.LAST_STEP_OUTCOME = {
        "taskId": task_id,
        "agent": agent,
        "laneBefore": lane_before,
        "laneAfter": lane_after,
        "toolFailures": 0,
        "ok": False,
        "message": "Ollama unavailable — confirm simulated result in the popup (10s).",
        "stopReason": "simulation_pending",
    }


def propose_simulation(proposal: Dict[str, Any]) -> str:
    """Return 'pending' (deferred) or 'applied' (ran immediately)."""
    ws = get_workflow_settings()
    if not ws.get("confirmSimulationFallback", True):
        _apply_default(proposal)
        return "applied"

    existing = getattr(state, "PENDING_SIMULATION", None)
    if isinstance(existing, dict) and existing.get("id"):
        add_system_log(
            "System",
            "warning",
            f"Replacing pending simulation {existing.get('id')} with {proposal.get('id')}",
        )
    proposal.setdefault("createdAt", _now())
    state.PENDING_SIMULATION = proposal
    add_system_log(
        "System",
        "info",
        f"Simulation pending confirm: {proposal.get('kind')} — {proposal.get('summary', '')[:120]}",
    )
    return "pending"


def try_defer_simulation(proposal: Dict[str, Any]) -> bool:
    return propose_simulation(proposal) == "pending"


def dismiss_pending_simulation() -> bool:
    if not getattr(state, "PENDING_SIMULATION", None):
        return False
    state.PENDING_SIMULATION = None
    add_system_log("System", "info", "Pending simulation dismissed — no changes applied.")
    return True


def _apply_sprint_dev_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import _simulate_dev_work
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(proposal.get("taskId") or ""))
    if task:
        _simulate_dev_work(task)


def _apply_sprint_cr_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import _simulate_code_review
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(proposal.get("taskId") or ""))
    if task:
        _simulate_code_review(task)


def _apply_sprint_qa_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import _simulate_qa
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(proposal.get("taskId") or ""))
    if task:
        _simulate_qa(task)


def _apply_po_plan_outline_default(proposal: Dict[str, Any]) -> None:
    from backend.services.events import publish_event
    from backend.services.project_service import save_current_project_state

    outline = (
        "## Summary\nOffline plan stub.\n\n## Approach\nScaffold core modules first.\n\n"
        "## Risks\nUnknown integration points.\n\n## Open questions\n(none)\n\n"
        "## Proposed epics\n"
        "- Project setup — workspace, tooling, and base deps so other slices can build\n"
        "- Core data model — entities and persistence for the main domain\n"
        "- Primary list / browse UI — user can view the main collection\n"
        "- Create & edit flows — add and update items with validation\n"
        "- Detail / summary view — inspect a single item or period\n"
        "- Export or sharing — take work out of the app (list, print, or share)\n"
    )
    state.PROJECT_PLAN_OUTLINE = outline
    for block in outline.split("\n\n"):
        stripped = block.strip()
        if stripped:
            publish_event("plan_chunk", {"chunk": stripped + "\n\n"})
    publish_event("plan_chunk", {"phase": "done", "outline": outline})
    add_system_log("Product Owner", "success", "Plan outline ready (offline confirm).")
    save_current_project_state()


def _apply_po_backlog_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import _append_po_backlog_from_output, existing_backlog_titles

    stub = json.dumps(
        {
            "epics": [
                {
                    "title": "Core scaffold",
                    "description": "Primary module structure.",
                    "children": [
                        {
                            "title": "Create core scaffold",
                            "description": "Primary module structure.",
                            "acceptanceCriteria": ["Entry point runs"],
                        }
                    ],
                }
            ]
        }
    )
    _append_po_backlog_from_output(stub, set(existing_backlog_titles()))


def _apply_refinement_dev_default(proposal: Dict[str, Any]) -> None:
    from backend.agents.task_context import find_task_by_id

    task_id = str(proposal.get("taskId") or "")
    task = find_task_by_id(task_id)
    if not task:
        return
    task["refinementDevReady"] = True
    task["refinementStatus"] = "dev_reviewed"
    task["refinementRoundTrips"] = int(task.get("refinementRoundTrips") or 0) + 1


def _apply_spike_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import _apply_spike_result
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(proposal.get("taskId") or ""))
    if task:
        _apply_spike_result(
            task,
            json.dumps(
                {
                    "findings": "Offline spike simulation.",
                    "recommendations": "",
                    "openQuestions": [],
                }
            ),
        )


def _apply_refinement_po_default(proposal: Dict[str, Any]) -> None:
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(str(proposal.get("taskId") or ""))
    if task:
        task["refinementStatus"] = "po_updated"


def _apply_po_clarification_default(proposal: Dict[str, Any]) -> None:
    from backend.agents.task_context import record_task_decision

    task_id = str(proposal.get("taskId") or "")
    record_task_decision(task_id, "Product Owner", "clarification", "Offline clarification")


def _apply_po_split_default(proposal: Dict[str, Any]) -> None:
    from backend.services.sprint_service import apply_backlog_from_po_response

    ctx = proposal.get("context") if isinstance(proposal.get("context"), dict) else {}
    po_output = str(ctx.get("poOutput") or "[]")
    task_id = str(proposal.get("taskId") or "")
    if task_id:
        apply_backlog_from_po_response(po_output, task_id)


def _apply_feature_intake_default(proposal: Dict[str, Any]) -> None:
    from backend.services.feature_service import intake_feature_offline

    ctx = proposal.get("context") if isinstance(proposal.get("context"), dict) else {}
    intake_feature_offline(
        str(ctx.get("title") or "Feature"),
        str(ctx.get("description") or ""),
        preferred_feature_id=ctx.get("preferredFeatureId"),
    )


def _apply_chat_default(proposal: Dict[str, Any]) -> str:
    from backend.services.events import publish_event

    text = "(Offline simulation — Ollama unavailable.)"
    ctx = proposal.get("context") if isinstance(proposal.get("context"), dict) else {}
    agent_role = str(ctx.get("agentRole") or proposal.get("agent") or "Developer")
    chat_agent_id = str(ctx.get("chatAgentId") or "")
    state.storage.save_chat_message(state.CURRENT_PROJECT_ID, "assistant", text, agent=agent_role)
    publish_event("chat", {"agent": chat_agent_id or agent_role, "response": text[:500]})
    return text


def _apply_generic_agent_text(proposal: Dict[str, Any], text: str) -> None:
    from backend.agents.task_context import record_task_decision

    task_id = str(proposal.get("taskId") or "")
    agent = str(proposal.get("agent") or "Agent")
    if task_id and task_id != "chat":
        record_task_decision(task_id, agent, "simulation_override", text[:500], text)


_KIND_DEFAULT: Dict[str, Any] = {
    "sprint_dev": _apply_sprint_dev_default,
    "sprint_cr": _apply_sprint_cr_default,
    "sprint_qa": _apply_sprint_qa_default,
    "po_plan_outline": _apply_po_plan_outline_default,
    "po_backlog": _apply_po_backlog_default,
    "refinement_dev": _apply_refinement_dev_default,
    "spike": _apply_spike_default,
    "refinement_po": _apply_refinement_po_default,
    "po_clarification": _apply_po_clarification_default,
    "po_split": _apply_po_split_default,
    "feature_intake": _apply_feature_intake_default,
    "chat": _apply_chat_default,
}


def _apply_default(proposal: Dict[str, Any]) -> None:
    kind = str(proposal.get("kind") or "")
    fn = _KIND_DEFAULT.get(kind)
    if fn:
        fn(proposal)
        return
    add_system_log("System", "warning", f"Unknown simulation kind {kind} — no default applied.")


def _apply_override(proposal: Dict[str, Any], target: str, value: str) -> None:
    from backend.agents.task_context import find_task_by_id, record_task_decision
    from backend.services.board_service import move_board_stage
    from backend.services.sprint_service import _append_po_backlog_from_output, existing_backlog_titles, set_qa_failure
    from backend.services.sprint_service import _commit_on_done
    from backend.workspace.files import write_workspace_file

    task_id = str(proposal.get("taskId") or "")
    t = target.strip().lower()
    val = str(value or "")

    if t == "skip_step":
        add_system_log("System", "info", "User skipped offline simulation.")
        return
    if t == "agent_text":
        if proposal.get("kind") == "chat":
            ctx = proposal.get("context") if isinstance(proposal.get("context"), dict) else {}
            agent_role = str(ctx.get("agentRole") or proposal.get("agent") or "Developer")
            chat_agent_id = str(ctx.get("chatAgentId") or "")
            state.storage.save_chat_message(state.CURRENT_PROJECT_ID, "assistant", val, agent=agent_role)
            from backend.services.events import publish_event

            publish_event("chat", {"agent": chat_agent_id or agent_role, "response": val[:500]})
        elif task_id:
            record_task_decision(
                task_id,
                str(proposal.get("agent") or "Agent"),
                "simulation_override",
                val[:500],
                val,
            )
        return
    if t == "dev_file_content":
        preview = proposal.get("defaultPreview") if isinstance(proposal.get("defaultPreview"), dict) else {}
        path = str(preview.get("fileName") or "index.js")
        write_workspace_file(path, val)
        if task_id:
            record_task_decision(task_id, "Developer", "completion", f"User override wrote {path}")
            lane = str(preview.get("targetLane") or "QA")
            move_board_stage(task_id, lane)
        return
    if t == "board_lane":
        if task_id:
            move_board_stage(task_id, val.strip())
        return
    if t == "qa_pass":
        if task_id:
            move_board_stage(task_id, "Done")
            task = find_task_by_id(task_id)
            if task:
                _commit_on_done(task)
            record_task_decision(task_id, "QA Tester", "qa", val[:200] or "User override QA pass")
        return
    if t == "qa_fail":
        if task_id:
            set_qa_failure(task_id, val[:200] or "User override QA fail", val[:500])
            move_board_stage(task_id, "In Progress")
            record_task_decision(task_id, "QA Tester", "qa_fail", val[:200])
        return
    if t == "po_output":
        if proposal.get("kind") == "po_plan_outline":
            state.PROJECT_PLAN_OUTLINE = val
            add_system_log("Product Owner", "success", "Plan outline set from user override.")
        elif proposal.get("kind") == "po_split":
            task_id = str(proposal.get("taskId") or "")
            if task_id:
                apply_backlog_from_po_response(val, task_id)
        else:
            _append_po_backlog_from_output(val, set(existing_backlog_titles()))
        return
    raise ValueError(f"Unsupported override target: {target}")


def apply_simulation_confirmation(
    *,
    accept: bool,
    override_target: Optional[str] = None,
    override_value: Optional[str] = None,
) -> Dict[str, Any]:
    proposal = getattr(state, "PENDING_SIMULATION", None)
    if not isinstance(proposal, dict) or not proposal.get("id"):
        return {"ok": False, "error": "No pending simulation to confirm."}

    task_id = str(proposal.get("taskId") or "")
    try:
        if accept:
            _apply_default(proposal)
        else:
            target = str(override_target or "").strip()
            if target not in VALID_OVERRIDE_TARGETS:
                return {"ok": False, "error": f"Invalid overrideTarget: {target}"}
            if target != "skip_step" and not str(override_value or "").strip():
                return {"ok": False, "error": "overrideValue required when declining default simulation."}
            _apply_override(proposal, target, str(override_value or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    finally:
        state.PENDING_SIMULATION = None

    from backend.services.project_service import save_current_project_state
    from backend.services.board_service import publish_board_update

    save_current_project_state()
    if task_id and task_id not in ("chat", "planning"):
        publish_board_update(task_id, source="simulation_confirm")
    add_system_log(
        "System",
        "success",
        f"Simulation confirmed ({'default' if accept else override_target}) for {proposal.get('kind')}",
    )
    return {"ok": True, "kind": proposal.get("kind"), "taskId": task_id}


def build_proposal(
    *,
    kind: str,
    task_id: str,
    agent: str,
    title: str,
    summary: str,
    default_preview: Dict[str, Any],
    source: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": new_proposal_id(),
        "taskId": task_id,
        "agent": agent,
        "kind": kind,
        "title": title,
        "summary": summary,
        "defaultPreview": default_preview,
        "source": source,
        "context": context or {},
        "createdAt": _now(),
    }
