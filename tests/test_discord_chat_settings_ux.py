"""Discord app_commands module import, chat keep-mounted, setting hints."""

from __future__ import annotations

from pathlib import Path


def test_discord_app_commands_is_module_level():
    import backend.services.discord_bot as bot

    # Must exist at module scope so typing.get_type_hints can resolve Choice annotations.
    assert hasattr(bot, "app_commands")
    src = Path(bot.__file__).read_text(encoding="utf-8")
    assert "from discord import app_commands" in src
    # Local import inside _make_discord_client was the NameError source — should be gone.
    assert "from discord import app_commands\n" in src or "from discord import app_commands" in src
    make_fn = src[src.index("def _make_discord_client") : src.index("async def start_discord_bot")]
    assert "from discord import app_commands" not in make_fn


def test_chat_keep_mounted_markers():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "chat-panel-host" in app
    assert "hidden={bottomTab !== 'chat'}" in app
    chat = (root / "frontend" / "src" / "components" / "ChatPanel.tsx").read_text(encoding="utf-8")
    assert "Working… (tools may take a few minutes)" in chat
    assert "Do not abort on unmount" in chat


def test_setting_hint_markers():
    root = Path(__file__).resolve().parents[1]
    hint = (root / "frontend" / "src" / "components" / "SettingHint.tsx").read_text(
        encoding="utf-8"
    )
    assert 'data-testid="setting-hint"' in hint
    settings = (root / "frontend" / "src" / "components" / "SettingsSlideOver.tsx").read_text(
        encoding="utf-8"
    )
    assert "SettingHint" in settings
    assert "OLLAMA URL" in settings
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "SettingHint" in panel
    assert "Require AC checklist before Done" in panel
    assert panel.count("<SettingHint") >= 15
