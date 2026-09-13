"""Rewrite nested project-create CLIs to the workspace root."""

from backend.services.command_policy import validate_command
from backend.services.scaffold_commands import rewrite_scaffold_command


def test_flutter_create_named_dir_rewritten_to_dot():
    cmd, note = rewrite_scaffold_command("flutter create mealplanner")
    assert cmd == "flutter create . --project-name mealplanner"
    assert note and "Workspace is the app root" in note


def test_flutter_create_dot_unchanged():
    original = "flutter create . --project-name mealplanner"
    cmd, note = rewrite_scaffold_command(original)
    assert cmd == original
    assert note is None


def test_flutter_create_packages_not_rewritten():
    original = "flutter create packages/ui"
    cmd, note = rewrite_scaffold_command(original)
    assert cmd == original
    assert note is None


def test_flutter_create_skipped_when_pubspec_exists(tmp_path, monkeypatch):
    from backend import state

    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "pubspec.yaml").write_text("name: existing\n", encoding="utf-8")
    cmd, note = rewrite_scaffold_command("flutter create mealplanner")
    assert cmd == "echo skipped_flutter_create_pubspec_exists"
    assert note and "already exists" in note


def test_dotnet_new_adds_output_dot():
    cmd, note = rewrite_scaffold_command("dotnet new webapi -n Foo --force")
    assert "-o ." in cmd
    assert note and "dotnet new" in note


def test_dotnet_new_keeps_existing_output():
    original = "dotnet new webapi -n Foo -o . --force"
    cmd, note = rewrite_scaffold_command(original)
    assert cmd == original
    assert note is None


def test_vite_create_named_dir_rewritten():
    cmd, note = rewrite_scaffold_command(
        "npm create vite@latest mealplanner -- --template react-ts"
    )
    assert cmd == "npm create vite@latest . -- --template react-ts"
    assert note and "Vite" in note


def test_cd_still_blocked():
    ok, reason = validate_command("cd mealplanner")
    assert ok is False
    assert "Directory changes" in reason


def test_scaffold_dotnet_command_uses_output_dot(monkeypatch):
    from backend.services import workspace_scaffold as ws_mod

    captured = {}

    def fake_run(command: str):
        captured["cmd"] = command
        return {"ok": True, "exit_code": 0, "summary": "ok", "output": ""}

    monkeypatch.setattr(ws_mod, "_run_allowlisted", fake_run)
    result = ws_mod.scaffold_dotnet({"title": "API"})
    assert "-o ." in result.get("command", "")
    assert captured["cmd"].endswith("--force") or "-o ." in captured["cmd"]
