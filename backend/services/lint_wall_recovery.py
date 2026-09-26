"""Deterministic recovery nudges and patches for common lint-wall patterns."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.services.logs import add_system_log

LINT_WALL_MARKER = "=== LINT WALL RECOVERY ==="

_FLUTTER_LINTS_URI_RE = re.compile(
    r"package:flutter_lints/flutter\.yaml",
    re.I,
)
_ANALYSIS_OPTIONS_RE = re.compile(r"analysis_options\.yaml", re.I)
_PUBSPEC_RE = re.compile(r"pubspec\.yaml", re.I)
_UNDEFINED_WIDGET_RE = re.compile(
    r"Undefined class ['\"]Widget['\"]",
    re.I,
)
_MAIN_DART_RE = re.compile(r"lib/main\.dart", re.I)


def _diag_lines(task: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for diagnostic in task.get("lastCommandDiagnostics") or []:
        if not isinstance(diagnostic, dict):
            continue
        for field in ("file", "path", "message"):
            raw = str(diagnostic.get(field) or "").strip()
            if raw:
                lines.append(raw)
    return lines


def detect_lint_wall_pattern(task: Dict[str, Any]) -> Optional[str]:
    """Return a pattern id when diagnostics match a known lint-wall fix."""
    if not isinstance(task, dict):
        return None
    blob = " ".join(_diag_lines(task))
    title = str(task.get("title") or "")
    lint_src = str(task.get("lintSourceFile") or "")
    combined = f"{title} {lint_src} {blob}"
    if _FLUTTER_LINTS_URI_RE.search(combined):
        if _PUBSPEC_RE.search(combined) or lint_src == "pubspec.yaml":
            return "flutter_lints_pubspec"
        return "flutter_lints_analysis_options"
    if title.startswith("Lint: ") and _ANALYSIS_OPTIONS_RE.search(title):
        return "flutter_lints_analysis_options"
    lower = combined.lower()
    if _UNDEFINED_WIDGET_RE.search(combined) or (
        "undefined class" in lower and "widget" in lower and _MAIN_DART_RE.search(combined)
    ):
        return "flutter_material_import"
    return None


def build_lint_wall_recovery_nudge(
    task: Dict[str, Any],
    *,
    target_path: str = "",
) -> str:
    """Inject exact fix instructions for known lint-wall patterns."""
    pattern = detect_lint_wall_pattern(task)
    path = str(target_path or "").strip()
    if pattern == "flutter_lints_pubspec":
        return (
            f"{LINT_WALL_MARKER}\n"
            "Missing dev_dependency `flutter_lints`. Next tool call MUST be apply_patch on "
            "`pubspec.yaml` adding under dev_dependencies:\n"
            "  flutter_lints: ^5.0.0\n"
            "Then run_command: flutter pub get"
        )
    if pattern == "flutter_material_import":
        return (
            f"{LINT_WALL_MARKER}\n"
            f"On `{path or 'lib/main.dart'}`: add `import 'package:flutter/material.dart';` "
            "at the top if missing, then fix the Widget usage.\n"
            "Next tool call MUST be apply_patch or write_file — no prose."
        )
    if pattern == "flutter_lints_analysis_options":
        pubspec_note = (
            "If pubspec.yaml lacks flutter_lints under dev_dependencies, patch pubspec.yaml first "
            "(flutter_lints: ^5.0.0), run flutter pub get, then fix analysis_options.yaml.\n"
        )
        fix = (
            f"{LINT_WALL_MARKER}\n"
            f"{pubspec_note}"
            f"On `{path or 'analysis_options.yaml'}`: ensure `include: package:flutter_lints/flutter.yaml` "
            "only after flutter_lints is in pubspec.yaml, OR temporarily comment out that include line.\n"
            "Next tool call MUST be apply_patch or write_file on the target file — no prose."
        )
        return fix
    if path:
        return (
            f"{LINT_WALL_MARKER}\n"
            f"Lint wall on `{path}`. read_file succeeded — next MUST be apply_patch or write_file."
        )
    return ""


_MISSING_PKG_URI_RE = re.compile(
    r"Target of URI doesn't exist: 'package:([^/]+)/",
    re.I,
)
_SKIP_PREFLIGHT_PACKAGES = frozenset(
    {"flutter", "flutter_test", "flutter_localizations", "flutter_web_plugins"}
)


def detect_missing_pubspec_package(task: Dict[str, Any]) -> Optional[str]:
    """Return pub package name when diagnostics show a missing package: URI."""
    if not isinstance(task, dict):
        return None
    for line in _diag_lines(task):
        match = _MISSING_PKG_URI_RE.search(line)
        if not match:
            continue
        pkg = str(match.group(1) or "").strip().lower()
        if pkg and pkg not in _SKIP_PREFLIGHT_PACKAGES:
            return pkg
    return None


def build_missing_package_preflight_nudge(task: Dict[str, Any]) -> str:
    pkg = detect_missing_pubspec_package(task)
    if not pkg:
        return ""
    return (
        f"{LINT_WALL_MARKER}\n"
        f"Missing dependency `{pkg}`. Patch `pubspec.yaml` to add `{pkg}` under dependencies "
        f"(with a compatible version), then run_command: flutter pub get "
        "before editing lib/ Dart files.\n"
        "Next tool call MUST be apply_patch on pubspec.yaml or run_command — no prose."
    )


def deterministic_lint_wall_patch(
    task: Dict[str, Any],
    path: str,
    file_content: str,
) -> Optional[Tuple[str, str, str]]:
    """
    Return (path, old_text, new_text) for a safe deterministic apply_patch when possible.
    Only applies when old_text is found verbatim in file_content.
    """
    pattern = detect_lint_wall_pattern(task)
    norm_path = str(path or "").replace("\\", "/")
    content = str(file_content or "")
    if pattern == "flutter_lints_pubspec" and norm_path.endswith("pubspec.yaml"):
        if "flutter_lints:" in content:
            return None
        anchor = "dev_dependencies:"
        if anchor not in content:
            return None
        old = anchor
        new = f"{anchor}\n  flutter_lints: ^5.0.0"
        return (norm_path, old, new)
    if pattern == "flutter_material_import" and norm_path.endswith("main.dart"):
        if "package:flutter/material.dart" in content:
            return None
        lines = content.splitlines()
        if not lines:
            old = ""
            new = "import 'package:flutter/material.dart';\n"
            return (norm_path, old, new)
        first = lines[0]
        if first.startswith("import "):
            old = first
            new = "import 'package:flutter/material.dart';\n" + first
            return (norm_path, old, new)
        old = first
        new = "import 'package:flutter/material.dart';\n" + first
        return (norm_path, old, new)
    if (
        pattern == "flutter_lints_analysis_options"
        and norm_path.endswith("analysis_options.yaml")
    ):
        line = "include: package:flutter_lints/flutter.yaml"
        if line not in content:
            return None
        old = line
        new = f"# {line}  # disabled until flutter_lints is added to pubspec.yaml"
        return (norm_path, old, new)
    return None


def _pubspec_add_dependency_patch(content: str, pkg: str) -> Optional[Tuple[str, str, str]]:
    if re.search(rf"^\s*{re.escape(pkg)}\s*:", content, re.M):
        return None
    anchor = "dependencies:"
    if anchor not in content:
        return None
    return ("pubspec.yaml", anchor, f"{anchor}\n  {pkg}: ^2.0.0")


def try_deterministic_missing_pubspec_fix(
    task: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> Optional[str]:
    """
    When diagnostics show a missing package: URI, patch pubspec.yaml and run flutter pub get
    before the Developer LLM loop. Returns a short log summary when work was done.
    """
    from backend import state

    pkg = detect_missing_pubspec_package(task)
    if not pkg:
        return None
    root = getattr(state, "WORKSPACE_DIR", None) or ""
    if not root:
        return None
    pubspec_path = os.path.join(root, "pubspec.yaml")
    if not os.path.isfile(pubspec_path):
        return None
    try:
        with open(pubspec_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None
    det = _pubspec_add_dependency_patch(content, pkg)
    if not det:
        return None
    det_path, old_text, new_text = det
    from backend.agents.registry import agent_dev
    from backend.services.step_diagnostics import get_active_trace, log_event
    from backend.services.tool_execution_service import execute_tool

    agent_id = str(getattr(agent_dev, "agent_id", None) or "dev")
    patch_result = execute_tool(
        agent_id,
        "apply_patch",
        {"path": det_path, "old_text": old_text, "new_text": new_text},
        task_id=task_id,
        source="agent",
        skip_approval=True,
    )
    if not bool(getattr(patch_result, "success", False)):
        return None
    trace = get_active_trace()
    if trace:
        trace.note_forced_tool_mode_effective()
        log_event("deterministic_pubspec_dependency", f"{pkg} → {det_path}")
    get_result = execute_tool(
        agent_id,
        "run_command",
        {"command": "flutter pub get"},
        task_id=task_id,
        source="agent",
        skip_approval=True,
    )
    get_ok = bool(getattr(get_result, "success", False))
    summary = str(getattr(get_result, "summary", "") or "")[:240]
    add_system_log(
        "Developer",
        "info",
        f"Deterministic pubspec fix: added {pkg}; flutter pub get "
        f"{'ok' if get_ok else 'failed'} — {summary}",
    )
    return f"Added `{pkg}` to pubspec.yaml and ran flutter pub get ({'ok' if get_ok else 'check logs'})."
