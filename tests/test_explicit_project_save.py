"""Guard that brief/config persist only on explicit Save."""

from pathlib import Path


def test_app_has_no_document_or_workflow_autosave():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "documentsSaveTimerRef" not in app
    assert "workflowSaveTimerRef" not in app
    assert "handleSaveAll" in app
    assert "patchProjectDocuments" in app
    assert "1500" not in app or "documentsSaveTimer" not in app
    assert "queuedWorkflowPatchPending" in app
    projects = (root / "backend" / "api" / "projects.py").read_text(encoding="utf-8")
    assert projects.count("build_state_response(include_files=False)") >= 2
