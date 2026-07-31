"""Optional Discord Gateway bot (outbound) for localhost quick actions.

Runs in-process with the FastAPI backend. Discord Gateway is outbound only —
no public Interactions HTTPS endpoint. Commands call AllHands services under
STATE_LOCK; they never open free-form agent chat or shell.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend import state
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

logger = logging.getLogger(__name__)

# Module-level so typing.get_type_hints (discord.py) can resolve Choice annotations.
try:
    import discord
    from discord import app_commands
except ImportError:  # pragma: no cover - optional dependency
    discord = None  # type: ignore[assignment]
    app_commands = None  # type: ignore[assignment]

# Slash command names (Discord: lowercase + hyphens).
CMD_STATUS = "ah-status"
CMD_PAUSE = "ah-pause"
CMD_RESUME = "ah-resume"
CMD_CANCEL = "ah-cancel"
CMD_BACKUP_DEV = "ah-backup-dev"
CMD_MODEL = "ah-model"
CMD_FEATURE = "ah-feature"
CMD_ANSWER = "ah-answer"
CMD_APPROVE = "ah-approve"
CMD_PENDING = "ah-pending"
CMD_CLAIM = "ah-claim"
CMD_EXTEND = "ah-extend"

_KNOWN_COMMANDS = frozenset(
    {
        CMD_STATUS,
        CMD_PAUSE,
        CMD_RESUME,
        CMD_CANCEL,
        CMD_BACKUP_DEV,
        CMD_MODEL,
        CMD_FEATURE,
        CMD_ANSWER,
        CMD_APPROVE,
        CMD_PENDING,
        CMD_CLAIM,
        CMD_EXTEND,
    }
)

_main_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_task: Optional[asyncio.Task] = None
_watchdog_task: Optional[asyncio.Task] = None
_client: Any = None
_bot_lock = threading.Lock()
_auto_sprint_thread: Optional[threading.Thread] = None
_auto_sprint_lock = threading.Lock()

_WATCHDOG_INTERVAL_SEC = 60

_bot_status: Dict[str, Any] = {
    "status": "idle",  # idle | connecting | ready | error | off
    "lastError": "",
    "readyAt": "",
}


def get_discord_bot_status() -> Dict[str, Any]:
    out = dict(_bot_status)
    out["running"] = discord_bot_running()
    return out


def _set_bot_status(status: str, *, last_error: str = "") -> None:
    _bot_status["status"] = status
    if last_error:
        _bot_status["lastError"] = str(last_error)[:300]
    elif status in ("ready", "idle", "off", "connecting"):
        if status != "error":
            _bot_status["lastError"] = ""
    if status == "ready":
        _bot_status["readyAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ollama_url() -> str:
    return str(getattr(state, "OLLAMA_URL", "") or "").strip() or "http://localhost:11434"


def is_actor_allowed(
    user_id: str,
    guild_id: Optional[str] = None,
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """Allow only configured user IDs; optional guild restriction for guild messages."""
    ws = settings if settings is not None else get_workflow_settings()
    allowed_raw = ws.get("discordBotAllowedUserIds") or []
    if not isinstance(allowed_raw, list):
        allowed_raw = []
    allowed = {str(x).strip() for x in allowed_raw if str(x).strip()}
    uid = str(user_id or "").strip()
    if not uid or uid not in allowed:
        return False
    configured_guild = str(ws.get("discordBotGuildId") or "").strip()
    if guild_id is not None and configured_guild:
        if str(guild_id).strip() != configured_guild:
            return False
    return True


def log_discord_action(
    actor_id: str,
    command: str,
    result: str,
    *,
    ok: bool = True,
) -> None:
    """Every Discord action is logged with source=discord."""
    summary = (result or "").replace("\n", " ").strip()
    if len(summary) > 240:
        summary = summary[:239] + "…"
    level = "info" if ok else "warning"
    add_system_log(
        "discord",
        level,
        f"source=discord actor={actor_id} command={command} ok={ok} result={summary}",
    )


def _active_in_progress_task(task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from backend.agents.task_context import find_task_by_id

    if task_id:
        return find_task_by_id(str(task_id).strip())
    lane = state.SHARED_BOARD.get("In Progress") or []
    if not lane:
        return None
    return lane[0] if isinstance(lane[0], dict) else None


def _first_needs_user_task(task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from backend.agents.task_context import find_task_by_id

    if task_id:
        tid = str(task_id).strip()
        task = find_task_by_id(tid)
        if not task:
            return None
        if not any(t.get("id") == tid for t in state.SHARED_BOARD.get("Needs User", []) or []):
            return None
        return task
    lane = state.SHARED_BOARD.get("Needs User") or []
    return lane[0] if lane and isinstance(lane[0], dict) else None


def is_auto_sprint_active() -> bool:
    """True if a background auto-sprint thread is alive or session is running."""
    with _auto_sprint_lock:
        thr = _auto_sprint_thread
        if thr is not None and thr.is_alive():
            return True
    try:
        from backend.services.sprint_session import _load_session

        session = _load_session()
        if session and session.get("status") == "running":
            return True
    except Exception:
        pass
    return False


def cmd_status(_options: Dict[str, Any]) -> str:
    from backend.services.board_status_digest import build_board_status_digest
    from backend.services.tool_approval import list_pending_approvals

    active = _active_in_progress_task()
    digest = build_board_status_digest(
        active_task=active,
        handler="idle" if not active else None,
        include_operator_extras=True,
    )
    cancel = bool(getattr(state, "SPRINT_CANCEL", False))
    intent = str(getattr(state, "SPRINT_CANCEL_INTENT", None) or "") or "none"
    pending = list_pending_approvals()
    bot = get_discord_bot_status()
    lines = [
        digest,
        f"sprintCancel={cancel} intent={intent} autoSprintActive={is_auto_sprint_active()}",
        f"pendingApprovals={len(pending)} discordBot={bot.get('status')}",
    ]
    return "\n".join(lines)


def cmd_pause(_options: Dict[str, Any]) -> str:
    state.SPRINT_CANCEL = True
    state.SPRINT_CANCEL_INTENT = "paused"
    return "Paused — auto-sprint cancel flag set (intent=paused). Use /ah-resume to continue."


def cmd_cancel(_options: Dict[str, Any]) -> str:
    state.SPRINT_CANCEL = True
    state.SPRINT_CANCEL_INTENT = "cancelled"
    return "Cancelled — auto-sprint cancel flag set (intent=cancelled; no auto-resume)."


def _start_auto_sprint_background() -> bool:
    """Start one background auto-sprint. Returns False if already active."""
    global _auto_sprint_thread
    from backend.services.sprint_service import run_auto_sprint

    with _auto_sprint_lock:
        if _auto_sprint_thread is not None and _auto_sprint_thread.is_alive():
            return False

        def _run() -> None:
            try:
                run_auto_sprint("", _ollama_url())
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("discord resume sprint failed")
                add_system_log(
                    "discord",
                    "error",
                    f"source=discord command={CMD_RESUME} sprint_error={exc}",
                )

        thr = threading.Thread(target=_run, name="discord-resume-sprint", daemon=True)
        _auto_sprint_thread = thr
        thr.start()
        return True


def cmd_resume(_options: Dict[str, Any]) -> str:
    if is_auto_sprint_active():
        return "Sprint already active — not starting another auto-sprint."

    try:
        from backend.services.sprint_session import dismiss_interrupted, get_recovery_context

        if get_recovery_context() is not None:
            dismiss_interrupted()
    except Exception:
        pass

    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    if not _start_auto_sprint_background():
        return "Sprint already active — not starting another auto-sprint."
    return "Resumed — cancel cleared; auto-sprint starting in background."


def cmd_backup_dev(options: Dict[str, Any]) -> str:
    from backend.services.backup_model import arm_backup_for_agent
    from backend.services.project_service import save_current_project_state

    task_id = options.get("task_id") or options.get("taskId")
    task = _active_in_progress_task(str(task_id) if task_id else None)
    if not task:
        return "No In Progress card found (pass task_id to target a specific card)."
    armed = arm_backup_for_agent("dev", task, reason="discord /ah-backup-dev", force=True)
    save_current_project_state()
    tid = str(task.get("id") or "?")
    title = str(task.get("title") or tid)
    if armed:
        return f"Backup Dev armed for [{tid}] {title}."
    return f"Could not arm Dev backup for [{tid}] {title} (check backup model settings)."


def cmd_model(options: Dict[str, Any]) -> str:
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.services.project_service import save_current_project_state

    preset = str(options.get("preset") or "").strip().lower()
    if preset not in ("fast", "quality"):
        return "Invalid preset — choose fast or quality."
    ws = get_workflow_settings()
    if preset == "fast":
        model = str(ws.get("discordModelPresetFast") or "qwen2.5-coder:7b").strip()
    else:
        model = str(ws.get("discordModelPresetQuality") or "qwen2.5-coder:14b").strip()
    if not model:
        return f"Preset '{preset}' has an empty model tag in settings."

    all_roles = bool(options.get("all_roles") or options.get("allRoles"))
    primary = dict(getattr(state, "PRIMARY_MODELS", {}) or {})
    if all_roles:
        for key, agent in (("po", agent_po), ("dev", agent_dev), ("cr", agent_cr), ("qa", agent_qa)):
            agent.model = model
            primary[key] = model
        state.PRIMARY_MODELS = primary
        save_current_project_state()
        return f"Model preset '{preset}' → {model} (all roles)."

    agent_dev.model = model
    primary["dev"] = model
    state.PRIMARY_MODELS = primary
    save_current_project_state()
    return f"Model preset '{preset}' → Dev primary = {model}."


def cmd_feature(options: Dict[str, Any]) -> str:
    from backend.services.feature_service import create_feature, find_feature_by_id, update_feature
    from backend.services.sprint_service import run_po_add_feature

    title = str(options.get("title") or "").strip()
    if not title:
        return "title is required."
    description = str(options.get("description") or options.get("body") or "").strip() or title
    feature_id = str(options.get("feature_id") or options.get("featureId") or "").strip() or None
    child_title = str(options.get("child_title") or options.get("childTitle") or "").strip()
    if not child_title:
        child_title = f"Implement: {title}"

    # Follow-up on an existing epic: prefer the shared PO intake path when Ollama is up;
    # otherwise update directly.
    if feature_id:
        existing = find_feature_by_id(feature_id)
        if not existing:
            return f"Feature not found: {feature_id}"
        try:
            run_po_add_feature(
                title,
                description,
                str(options.get("ollama_url") or "http://localhost:11434"),
                preferred_feature_id=feature_id,
            )
            return (
                f"Follow-up for feature [{feature_id}] '{existing.get('title', title)}' "
                "sent to PO (update preferred)."
            )
        except Exception:
            feature, child = update_feature(
                feature_id,
                title=str(existing.get("title") or title),
                description=str(existing.get("description") or description),
                request_title=title,
                request_body=description,
                child_task={
                    "title": child_title,
                    "description": description,
                    "acceptanceCriteria": [],
                },
                po_summary="Updated via Discord /ah-feature (feature_id)",
                source="discord",
            )
            cid = str(child.get("id") or "?")
            return (
                f"Updated feature [{feature_id}] with backlog child [{cid}] {child_title}. "
                "Sprint not started."
            )

    feature, child = create_feature(
        title,
        description,
        request_title=title,
        request_body=description,
        child_task={
            "title": child_title,
            "description": description,
            "acceptanceCriteria": [],
        },
        po_summary="Created via Discord /ah-feature",
        source="discord",
    )
    fid = str(feature.get("id") or "?")
    cid = str(child.get("id") or "?")
    return (
        f"Draft feature [{fid}] {title} created with backlog child [{cid}] {child_title}. "
        "Sprint not started."
    )


def cmd_answer(options: Dict[str, Any]) -> str:
    """Resolve a Needs User card (same path as POST /api/tasks/{id}/resolve-user)."""
    from backend.agents.task_context import (
        init_refinement_fields,
        normalize_task,
        record_task_decision,
        record_task_transcript,
    )
    from backend.services.board_service import move_board_stage
    from backend.services.logs import add_system_log as _log
    from backend.services.needs_user_guard import append_user_resolution, set_needs_user_cooldown

    answer = str(options.get("answer") or "").strip()
    if not answer:
        return "answer is required."
    target = str(options.get("target") or "dev").strip().lower()
    if target not in ("dev", "refinement", "po"):
        return "target must be dev, refinement, or po."
    lane_map = {"dev": "In Progress", "refinement": "Refinement", "po": "Needs PO"}
    target_lane = lane_map[target]

    task_id_opt = options.get("task_id") or options.get("taskId")
    task = _first_needs_user_task(str(task_id_opt) if task_id_opt else None)
    if not task:
        return "No Needs User card found (pass task_id or move a card to Needs User)."
    task_id = str(task.get("id") or "")
    normalize_task(task)
    prior_question = (
        task.get("userQuestion")
        or task.get("needsUserReason")
        or task.get("needsUserAction")
        or ""
    )
    append_user_resolution(task, str(prior_question), answer, target_lane)
    set_needs_user_cooldown(task)
    task["needsUserDuplicate"] = False
    record_task_transcript(
        task_id,
        "user",
        f"User response (→ {target_lane}):\n{answer}",
        agent="User",
    )
    task["userQuestion"] = None
    task["needsUserReason"] = None
    task["needsUserAction"] = None
    record_task_decision(
        task_id,
        "User",
        "resolve",
        f"User routed to {target_lane} (discord)",
        answer[:500],
    )
    if target == "refinement":
        init_refinement_fields(task)
        task["refinementStatus"] = "pending"
        task["refinementNotes"] = answer
    move_board_stage(task_id, target_lane)
    _log("System", "success", f"User resolved {task_id} → {target_lane} (discord)")
    return f"Answered [{task_id}] → {target_lane}."


def cmd_approve(options: Dict[str, Any]) -> str:
    from backend.agents.task_context import find_task_by_id, record_task_decision, sort_backlog
    from backend.services.board_service import move_board_stage
    from backend.services.tool_approval import list_pending_approvals, resolve_tool_approval

    kind = str(options.get("kind") or "tool").strip().lower()
    if kind in ("card", "feature", "pending"):
        task_id = str(options.get("task_id") or options.get("taskId") or options.get("approval_id") or "").strip()
        lane = state.SHARED_BOARD.get("Pending Approval") or []
        if not task_id:
            if not lane:
                return "No Pending Approval cards."
            task_id = str(lane[0].get("id") or "")
        if not task_id:
            return "No Pending Approval cards."
        if not any(str(t.get("id")) == task_id for t in lane if isinstance(t, dict)):
            return f"Task {task_id} is not in Pending Approval."
        move_board_stage(task_id, "Backlog")
        sort_backlog()
        record_task_decision(task_id, "User", "approve", "User approved feature for development (discord)")
        add_system_log("System", "success", f"Approved {task_id} → Backlog (discord)")
        return f"Card [{task_id}] approved → Backlog."

    decision_raw = str(options.get("decision") or "approve").strip().lower()
    if decision_raw in ("approve", "yes", "y", "true", "1"):
        approved = True
    elif decision_raw in ("deny", "reject", "no", "n", "false", "0"):
        approved = False
    else:
        return "decision must be approve or deny."

    approval_id = str(options.get("approval_id") or options.get("approvalId") or "").strip()
    pending = list_pending_approvals()
    if not approval_id:
        if not pending:
            return "No pending tool approvals."
        approval_id = str(pending[0].get("id") or "")
    if not approval_id:
        return "No pending tool approvals."
    ok = resolve_tool_approval(approval_id, approved)
    remaining = list_pending_approvals()
    if not ok:
        return f"Approval {approval_id} not found or already resolved. pending={len(remaining)}"
    verb = "approved" if approved else "denied"
    return f"Tool approval {approval_id} {verb}. pending={len(remaining)}"


def cmd_pending(_options: Dict[str, Any]) -> str:
    from backend.services.tool_approval import list_pending_approvals

    lines: List[str] = []
    needs = state.SHARED_BOARD.get("Needs User") or []
    if needs:
        lines.append(f"Needs User ({len(needs)}):")
        for t in needs[:8]:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or "?")
            q = str(t.get("userQuestion") or t.get("needsUserReason") or t.get("title") or "")
            q = q.replace("\n", " ").strip()
            if len(q) > 80:
                q = q[:79] + "…"
            lines.append(f"  [{tid}] {q}")
    else:
        lines.append("Needs User: (none)")

    pending_cards = state.SHARED_BOARD.get("Pending Approval") or []
    if pending_cards:
        lines.append(f"Pending Approval ({len(pending_cards)}):")
        for t in pending_cards[:8]:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or "?")
            title = str(t.get("title") or "").replace("\n", " ").strip()
            if len(title) > 60:
                title = title[:59] + "…"
            lines.append(f"  [{tid}] {title}")
    else:
        lines.append("Pending Approval: (none)")

    pending = list_pending_approvals()
    if pending:
        lines.append(f"Tool approvals ({len(pending)}):")
        for p in pending[:8]:
            pid = str(p.get("id") or "?")
            tool = str(p.get("toolName") or "?")
            tid = str(p.get("taskId") or "")
            extra = f" task={tid}" if tid else ""
            lines.append(f"  [{pid}] {tool}{extra}")
    else:
        lines.append("Tool approvals: (none)")
    return "\n".join(lines)


def cmd_claim(options: Dict[str, Any]) -> str:
    from backend.agents.agent_run import get_active_run
    from backend.agents.task_context import count_claimable_backlog_tasks
    from backend.services.board_service import claim_ready_backlog_tasks

    if get_active_run() is not None:
        return "Cannot claim while an agent sprint step is running."
    try:
        limit = int(options.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(20, limit))
    if count_claimable_backlog_tasks() == 0:
        return "No claimable backlog cards."
    claimed = claim_ready_backlog_tasks(limit=limit)
    remaining = count_claimable_backlog_tasks()
    if not claimed:
        return "No cards claimed."
    return f"Claimed {len(claimed)}: {', '.join(claimed)}. readyRemaining={remaining}"


def cmd_extend(options: Dict[str, Any]) -> str:
    from backend.services.prompt_retry import extend_agent_step
    from backend.services.sprint_session import get_recovery_context

    task_id = str(options.get("task_id") or options.get("taskId") or "").strip()
    if not task_id:
        active = _active_in_progress_task()
        if active:
            task_id = str(active.get("id") or "")
        if not task_id:
            recovery = get_recovery_context() or {}
            task_id = str(recovery.get("taskId") or "")
    if not task_id:
        return "No task_id — pass task_id or have an In Progress / recovery card."
    try:
        extra = int(options.get("extra") or options.get("extra_iterations") or 4)
    except (TypeError, ValueError):
        extra = 4
    extra = max(1, min(16, extra))
    result = extend_agent_step(
        task_id,
        "dev",
        _ollama_url(),
        action="extend",
        extra_iterations=extra,
    )
    if result.get("ok") is False:
        return f"Extend failed: {result.get('error') or result}"
    return f"Extended [{task_id}] by +{extra} iterations."


_HANDLERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    CMD_STATUS: cmd_status,
    CMD_PAUSE: cmd_pause,
    CMD_RESUME: cmd_resume,
    CMD_CANCEL: cmd_cancel,
    CMD_BACKUP_DEV: cmd_backup_dev,
    CMD_MODEL: cmd_model,
    CMD_FEATURE: cmd_feature,
    CMD_ANSWER: cmd_answer,
    CMD_APPROVE: cmd_approve,
    CMD_PENDING: cmd_pending,
    CMD_CLAIM: cmd_claim,
    CMD_EXTEND: cmd_extend,
}


def dispatch_command(
    command: str,
    *,
    actor_id: str,
    guild_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Dispatch a fixed slash command. Returns (ok, reply_text).
    Rejects unknown commands and non-allowlisted actors.
    """
    name = str(command or "").strip().lstrip("/").lower()
    opts = dict(options or {})
    ws = settings if settings is not None else get_workflow_settings()

    if not is_actor_allowed(actor_id, guild_id, settings=ws):
        msg = "Not authorized — your Discord user id is not allowlisted."
        log_discord_action(actor_id, name or "?", msg, ok=False)
        return False, msg

    if name not in _KNOWN_COMMANDS:
        msg = f"Unknown command: {name}"
        log_discord_action(actor_id, name or "?", msg, ok=False)
        return False, msg

    handler = _HANDLERS[name]
    try:
        with state.STATE_LOCK:
            reply = handler(opts)
        log_discord_action(actor_id, name, reply, ok=True)
        return True, reply
    except Exception as exc:
        logger.exception("discord command %s failed", name)
        msg = f"Error running /{name}: {exc}"
        log_discord_action(actor_id, name, msg, ok=False)
        return False, msg


def _make_discord_client(guild_id: str) -> Any:
    if discord is None or app_commands is None:
        raise ImportError("discord.py not installed")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    async def _reply(interaction: discord.Interaction, command: str, options: Dict[str, Any]) -> None:
        await interaction.response.defer(ephemeral=True)
        actor = str(interaction.user.id)
        gid = str(interaction.guild_id) if interaction.guild_id else None
        _ok, text = await asyncio.to_thread(
            dispatch_command,
            command,
            actor_id=actor,
            guild_id=gid,
            options=options,
        )
        if len(text) > 1900:
            text = text[:1899] + "…"
        await interaction.followup.send(text, ephemeral=True)

    @tree.command(name=CMD_STATUS, description="AllHands board status digest")
    async def _status(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_STATUS, {})

    @tree.command(name=CMD_PAUSE, description="Pause current auto-sprint")
    async def _pause(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_PAUSE, {})

    @tree.command(name=CMD_RESUME, description="Clear cancel and start auto-sprint")
    async def _resume(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_RESUME, {})

    @tree.command(name=CMD_CANCEL, description="Cancel auto-sprint (no auto-resume)")
    async def _cancel(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_CANCEL, {})

    @tree.command(name=CMD_BACKUP_DEV, description="Arm Dev backup model for In Progress card")
    @app_commands.describe(task_id="Optional task id (defaults to first In Progress card)")
    async def _backup(interaction: discord.Interaction, task_id: Optional[str] = None) -> None:
        opts: Dict[str, Any] = {}
        if task_id:
            opts["task_id"] = task_id
        await _reply(interaction, CMD_BACKUP_DEV, opts)

    @tree.command(name=CMD_MODEL, description="Apply Dev model preset (fast or quality)")
    @app_commands.describe(
        preset="fast or quality (from Workflow settings presets)",
        all_roles="Also apply preset to PO, CR, and QA",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(name="fast", value="fast"),
            app_commands.Choice(name="quality", value="quality"),
        ]
    )
    async def _model(
        interaction: discord.Interaction,
        preset: app_commands.Choice[str],
        all_roles: bool = False,
    ) -> None:
        await _reply(
            interaction,
            CMD_MODEL,
            {"preset": preset.value, "all_roles": all_roles},
        )

    @tree.command(name=CMD_FEATURE, description="Create or follow up on a Feature (+ backlog child); does not start sprint")
    @app_commands.describe(
        title="Feature / follow-up title",
        description="Optional description",
        child_title="Optional first backlog child title",
        feature_id="Optional existing Features-lane id to amend (follow-up)",
    )
    async def _feature(
        interaction: discord.Interaction,
        title: str,
        description: Optional[str] = None,
        child_title: Optional[str] = None,
        feature_id: Optional[str] = None,
    ) -> None:
        opts: Dict[str, Any] = {"title": title}
        if description:
            opts["description"] = description
        if child_title:
            opts["child_title"] = child_title
        if feature_id:
            opts["feature_id"] = feature_id
        await _reply(interaction, CMD_FEATURE, opts)

    @tree.command(name=CMD_ANSWER, description="Answer a Needs User card")
    @app_commands.describe(
        answer="Your answer / decision",
        task_id="Optional task id (defaults to first Needs User card)",
        target="Where to route: dev, refinement, or po",
    )
    @app_commands.choices(
        target=[
            app_commands.Choice(name="dev", value="dev"),
            app_commands.Choice(name="refinement", value="refinement"),
            app_commands.Choice(name="po", value="po"),
        ]
    )
    async def _answer(
        interaction: discord.Interaction,
        answer: str,
        task_id: Optional[str] = None,
        target: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        opts: Dict[str, Any] = {"answer": answer, "target": target.value if target else "dev"}
        if task_id:
            opts["task_id"] = task_id
        await _reply(interaction, CMD_ANSWER, opts)

    @tree.command(name=CMD_APPROVE, description="Approve tool request or Pending Approval card")
    @app_commands.describe(
        kind="tool (default) or card",
        decision="approve or deny (tool only)",
        approval_id="Tool approval id or card task id",
        task_id="Pending Approval card id (kind=card)",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="tool", value="tool"),
            app_commands.Choice(name="card", value="card"),
        ],
        decision=[
            app_commands.Choice(name="approve", value="approve"),
            app_commands.Choice(name="deny", value="deny"),
        ],
    )
    async def _approve(
        interaction: discord.Interaction,
        kind: Optional[app_commands.Choice[str]] = None,
        decision: Optional[app_commands.Choice[str]] = None,
        approval_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        opts: Dict[str, Any] = {"kind": kind.value if kind else "tool"}
        if decision:
            opts["decision"] = decision.value
        if approval_id:
            opts["approval_id"] = approval_id
        if task_id:
            opts["task_id"] = task_id
        await _reply(interaction, CMD_APPROVE, opts)

    @tree.command(name=CMD_PENDING, description="List Needs User, Pending Approval, and tool approvals")
    async def _pending(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_PENDING, {})

    @tree.command(name=CMD_CLAIM, description="Claim ready backlog cards into In Progress")
    @app_commands.describe(limit="Max cards to claim (default 3)")
    async def _claim(interaction: discord.Interaction, limit: Optional[int] = None) -> None:
        opts: Dict[str, Any] = {}
        if limit is not None:
            opts["limit"] = limit
        await _reply(interaction, CMD_CLAIM, opts)

    @tree.command(name=CMD_EXTEND, description="Extend current Dev step iterations")
    @app_commands.describe(
        task_id="Optional task id (defaults to In Progress / recovery)",
        extra="Extra iterations (default 4)",
    )
    async def _extend(
        interaction: discord.Interaction,
        task_id: Optional[str] = None,
        extra: Optional[int] = None,
    ) -> None:
        opts: Dict[str, Any] = {}
        if task_id:
            opts["task_id"] = task_id
        if extra is not None:
            opts["extra"] = extra
        await _reply(interaction, CMD_EXTEND, opts)

    @client.event
    async def on_ready() -> None:
        _set_bot_status("ready")
        add_system_log(
            "discord",
            "info",
            f"source=discord bot_ready user={getattr(client.user, 'id', '?')}",
        )
        try:
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                tree.copy_global_to(guild=guild)
                synced = await tree.sync(guild=guild)
            else:
                synced = await tree.sync()
            names = [c.name for c in synced]
            add_system_log(
                "discord",
                "info",
                f"source=discord slash_synced count={len(names)} commands={','.join(names)}",
            )
        except Exception as exc:
            logger.exception("discord slash sync failed")
            # Keep Gateway status ready if we already connected; surface sync error only.
            if _bot_status.get("status") == "ready":
                _bot_status["lastError"] = f"slash_sync: {exc}"[:300]
            else:
                _set_bot_status("error", last_error=f"slash_sync: {exc}")
            add_system_log("discord", "error", f"source=discord slash_sync_error={exc}")

    @client.event
    async def on_disconnect() -> None:
        _set_bot_status("connecting", last_error="gateway disconnected — reconnecting")
        add_system_log(
            "discord",
            "warning",
            "source=discord gateway_disconnected",
        )

    @client.event
    async def on_resumed() -> None:
        _set_bot_status("ready")
        add_system_log("discord", "info", "source=discord gateway_resumed")

    return client


async def _discord_watchdog_loop() -> None:
    """If bot is enabled but the Gateway task died, restart it."""
    while True:
        try:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        ws = get_workflow_settings()
        if not ws.get("discordBotEnabled"):
            continue
        with _bot_lock:
            task = _bot_task
            dead = task is None or task.done()
        if not dead:
            client = _client
            if client is not None:
                try:
                    if getattr(client, "is_closed", lambda: False)():
                        dead = True
                except Exception:
                    pass
        if dead:
            add_system_log(
                "discord",
                "warning",
                "source=discord watchdog_restarting_dead_bot",
            )
            _set_bot_status("connecting", last_error="watchdog restarting bot")
            try:
                await start_discord_bot()
            except Exception as exc:
                logger.exception("discord watchdog restart failed")
                _set_bot_status("error", last_error=f"watchdog: {exc}")


def _ensure_watchdog() -> None:
    global _watchdog_task
    loop = _main_loop
    if loop is None or not loop.is_running():
        return
    with _bot_lock:
        if _watchdog_task is not None and not _watchdog_task.done():
            return
        _watchdog_task = asyncio.create_task(_discord_watchdog_loop(), name="discord-watchdog")


async def start_discord_bot() -> None:
    """Start Gateway bot from workflow settings (no-op if disabled)."""
    global _main_loop, _bot_task, _client
    _main_loop = asyncio.get_running_loop()
    _ensure_watchdog()

    ws = get_workflow_settings()
    if not ws.get("discordBotEnabled"):
        _set_bot_status("off")
        return
    from backend.services.api_auth import resolve_discord_bot_token

    token = resolve_discord_bot_token(str(ws.get("discordBotToken") or ""))
    if not token:
        _set_bot_status("error", last_error="enabled but token missing")
        add_system_log(
            "discord",
            "warning",
            "source=discord bot_enabled_but_token_missing",
        )
        return
    if discord is None or app_commands is None:
        _set_bot_status("error", last_error="discord.py not installed")
        add_system_log(
            "discord",
            "error",
            "source=discord discord_py_not_installed — pip install discord.py",
        )
        return

    guild_id = str(ws.get("discordBotGuildId") or "").strip()
    _set_bot_status("connecting")

    async def _runner() -> None:
        global _client
        client = _make_discord_client(guild_id)
        _client = client
        try:
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("discord bot crashed")
            _set_bot_status("error", last_error=str(exc))
            add_system_log("discord", "error", f"source=discord bot_error={exc}")
        finally:
            if _client is client:
                _client = None
            if _bot_status.get("status") == "ready":
                _set_bot_status("idle")

    with _bot_lock:
        if _bot_task and not _bot_task.done():
            return
        _bot_task = asyncio.create_task(_runner(), name="discord-bot")


async def stop_discord_bot() -> None:
    """Stop Gateway bot if running."""
    global _bot_task, _client, _watchdog_task
    with _bot_lock:
        client = _client
        task = _bot_task
        watchdog = _watchdog_task
        _client = None
        _bot_task = None
        _watchdog_task = None
    if client is not None:
        try:
            await client.close()
        except Exception:
            logger.debug("discord client close failed", exc_info=True)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if watchdog is not None and not watchdog.done():
        watchdog.cancel()
        try:
            await watchdog
        except (asyncio.CancelledError, Exception):
            pass
    ws = get_workflow_settings()
    if not ws.get("discordBotEnabled"):
        _set_bot_status("off")
    elif _bot_status.get("status") not in ("error",):
        _set_bot_status("idle")


async def reload_discord_bot() -> None:
    """Stop and re-read settings, then start if still enabled."""
    await stop_discord_bot()
    await start_discord_bot()


def schedule_discord_bot_reload() -> None:
    """Thread-safe schedule of bot reload on the FastAPI event loop."""
    loop = _main_loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(reload_discord_bot(), loop)


def discord_bot_running() -> bool:
    task = _bot_task
    return bool(task is not None and not task.done())


def known_commands() -> List[str]:
    return sorted(_KNOWN_COMMANDS)
