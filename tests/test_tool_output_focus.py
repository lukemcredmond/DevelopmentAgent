from backend.services.tool_output_focus import (
    format_read_file_for_llm,
    manifest_focus_block,
    read_file_observation_line,
)


def test_manifest_focus_extracts_dependencies():
    body = """name: meal_app
environment:
  sdk: ">=3.0.0"
dependencies:
  flutter:
    sdk: flutter
  firebase_core: ^3.0.0
dev_dependencies:
  flutter_test:
    sdk: flutter
"""
    focus = manifest_focus_block("pubspec.yaml", body)
    assert "name: meal_app" in focus
    assert "firebase_core" in focus
    assert "FOCUS" in focus


def test_format_read_file_po_needs_po_header():
    text = format_read_file_for_llm(
        "pubspec.yaml",
        "name: x\ndependencies:\n  foo: ^1.0.0\n",
        agent_role="Product Owner",
        task_lane="Needs PO",
    )
    assert "=== read_file: pubspec.yaml ===" in text
    assert "Do NOT call read_file" in text
    assert "FOCUS" in text
    assert "foo: ^1.0.0" in text


def test_pubspec_yml_error_hint():
    line = read_file_observation_line(
        "pubspec.yml",
        "Error: File 'pubspec.yml' not found.",
    )
    assert "pubspec.yaml" in line
    assert "FAIL" in line


def test_observation_line_manifest():
    line = read_file_observation_line(
        "pubspec.yaml",
        "name: app\ndependencies:\n  path: ^1.0\n",
    )
    assert "path=pubspec.yaml" in line
    assert "dependencies" in line.lower() or "path:" in line
