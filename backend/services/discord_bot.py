"""Optional Discord Gateway bot (outbound) for localhost quick actions.

Runs in-process with the FastAPI backend. Discord Gateway is outbound only —
no public Interactions HTTPS endpoint. Commands call AllHands services under
STATE_LOCK; they never open free-form agent chat or shell.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend import state
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

logger = logging.getLogger(__name__)

# Slash command names (Discord: lowercase + hyphens).
CMD_STATUS = "ah-status"
CMD_PAUSE = "ah-pause"
CMD_RESUME = "ah-resume"
CMD_CANCEL = "ah-cancel"
CMD_BACKUP_DEV = "ah-backup-dev"
CMD_MODEL = "ah-model"
CMD_FEATURE = "ah-feature"

_KNOWN_COMMANDS = frozenset(
    {
        CMD_STATUS,
        CMD_PAUSE,
        CMD_RESUME,
        CMD_CANCEL,
        CMD_BACKUP_DEV,
        CMD_MODEL,
        CMD_FEATURE,
    }
)

_main_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_task: Optional[asyncio.Task] = None
_client: Any = None
_bot_lock = threading.Lock()


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
    # DMs: allowlisted users only (already checked). Guild messages: must match if set.
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
        task = find_task_by_id(str(task_id).strip())
        return task
    lane = state.SHARED_BOARD.get("In Progress") or []
    if not lane:
        return None
    return lane[0] if isinstance(lane[0], dict) else None


def cmd_status(_options: Dict[str, Any]) -> str:
    from backend.services.board_status_digest import build_board_status_digest

    active = _active_in_progress_task()
    digest = build_board_status_digest(active_task=active, handler="idle" if not active else None)
    cancel = bool(getattr(state, "SPRINT_CANCEL", False))
    flags = f"sprintCancel={cancel}"
    return f"{digest}\n{flags}"


def cmd_pause(_options: Dict[str, Any]) -> str:
    state.SPRINT_CANCEL = True
    return "Paused — auto-sprint cancel flag set. Use /ah-resume to continue."


def cmd_cancel(_options: Dict[str, Any]) -> str:
    state.SPRINT_CANCEL = True
    return "Cancelled — auto-sprint cancel flag set (no auto-resume)."


def _start_auto_sprint_background() -> None:
    from backend.services.sprint_service import run_auto_sprint

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

    threading.Thread(target=_run, name="discord-resume-sprint", daemon=True).start()


def cmd_resume(_options: Dict[str, Any]) -> str:
    state.SPRINT_CANCEL = False
    _start_auto_sprint_background()
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
    from backend.services.feature_service import create_feature

    title = str(options.get("title") or "").strip()
    if not title:
        return "title is required."
    description = str(options.get("description") or "").strip() or title
    child_title = str(options.get("child_title") or options.get("childTitle") or "").strip()
    if not child_title:
        child_title = f"Implement: {title}"
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


_HANDLERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    CMD_STATUS: cmd_status,
    CMD_PAUSE: cmd_pause,
    CMD_RESUME: cmd_resume,
    CMD_CANCEL: cmd_cancel,
    CMD_BACKUP_DEV: cmd_backup_dev,
    CMD_MODEL: cmd_model,
    CMD_FEATURE: cmd_feature,
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
    import discord
    from discord import app_commands

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
        # Discord message limit 2000
        if len(text) > 1900:
            text = text[:1899] + "…"
        await interaction.followup.send(text, ephemeral=True)

    @tree.command(name=CMD_STATUS, description="AllHands board status digest")
    async def _status(interaction: discord.Interaction) -> None:
        await _reply(interaction, CMD_STATUS, {})

    @tree.command(name=CMD_PAUSE, description="Pause / cancel current auto-sprint")
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

    @tree.command(name=CMD_FEATURE, description="Create draft Feature (+ backlog child); does not start sprint")
    @app_commands.describe(
        title="Feature title",
        description="Optional description",
        child_title="Optional first backlog child title",
    )
    async def _feature(
        interaction: discord.Interaction,
        title: str,
        description: Optional[str] = None,
        child_title: Optional[str] = None,
    ) -> None:
        opts: Dict[str, Any] = {"title": title}
        if description:
            opts["description"] = description
        if child_title:
            opts["child_title"] = child_title
        await _reply(interaction, CMD_FEATURE, opts)

    @client.event
    async def on_ready() -> None:
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
            add_system_log("discord", "error", f"source=discord slash_sync_error={exc}")

    return client


async def start_discord_bot() -> None:
    """Start Gateway bot from workflow settings (no-op if disabled)."""
    global _main_loop, _bot_task, _client
    _main_loop = asyncio.get_running_loop()

    ws = get_workflow_settings()
    if not ws.get("discordBotEnabled"):
        return
    token = str(ws.get("discordBotToken") or "").strip()
    if not token:
        add_system_log(
            "discord",
            "warning",
            "source=discord bot_enabled_but_token_missing",
        )
        return
    try:
        import discord  # noqa: F401
    except ImportError:
        add_system_log(
            "discord",
            "error",
            "source=discord discord_py_not_installed — pip install discord.py",
        )
        return

    guild_id = str(ws.get("discordBotGuildId") or "").strip()

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
            add_system_log("discord", "error", f"source=discord bot_error={exc}")
        finally:
            if _client is client:
                _client = None

    with _bot_lock:
        if _bot_task and not _bot_task.done():
            return
        _bot_task = asyncio.create_task(_runner(), name="discord-bot")


async def stop_discord_bot() -> None:
    """Stop Gateway bot if running."""
    global _bot_task, _client
    with _bot_lock:
        client = _client
        task = _bot_task
        _client = None
        _bot_task = None
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
