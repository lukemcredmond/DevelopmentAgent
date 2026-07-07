"""dotnet test and lint detection for workspace heuristics."""

from unittest.mock import MagicMock, patch

from backend.bootstrap import initialize
from backend.workspace.files import (
    _dotnet_test_commands,
    _workspace_has_dotnet_project,
    derive_project_lint_command,
    run_tests_on_workspace,
)


def test_workspace_has_dotnet_project(tmp_path):
    assert not _workspace_has_dotnet_project(str(tmp_path))
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    assert _workspace_has_dotnet_project(str(tmp_path))


def test_dotnet_test_commands_prefers_sln(tmp_path):
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    cmds = _dotnet_test_commands(str(tmp_path), "README.md")
    assert cmds == [["dotnet", "test", str(tmp_path / "App.sln")]]


def test_dotnet_test_commands_explicit_csproj(tmp_path):
    proj = tmp_path / "tests" / "Unit.csproj"
    proj.parent.mkdir(parents=True)
    proj.write_text("<Project />", encoding="utf-8")
    rel = "tests/Unit.csproj"
    cmds = _dotnet_test_commands(str(tmp_path), rel)
    assert cmds == [["dotnet", "test", str(proj)]]


def test_derive_project_lint_command_dotnet(tmp_path, monkeypatch):
    initialize()
    from backend import state

    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    state.VIRTUAL_FILESYSTEM["App.sln"] = ""
    assert derive_project_lint_command() == "dotnet build"


@patch("subprocess.run")
def test_run_tests_on_workspace_dotnet(mock_run, tmp_path, monkeypatch):
    initialize()
    from backend import state

    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    (tmp_path / "App.sln").write_text("", encoding="utf-8")
    state.VIRTUAL_FILESYSTEM["App.sln"] = ""

    mock_run.return_value = MagicMock(returncode=0, stdout="Passed!", stderr="")

    result = run_tests_on_workspace("App.sln")
    assert "Tests passed" in result
    mock_run.assert_called()
    args = mock_run.call_args[0][0]
    assert args[0] == "dotnet"
    assert args[1] == "test"
