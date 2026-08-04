"""Focused tool output shaping before messages reach the LLM."""

from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

_MANIFEST_SUFFIXES = (
    "pubspec.yaml",
    "pubspec.yml",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
)


def is_dependency_manifest_path(path: str) -> bool:
    p = (path or "").lower().replace("\\", "/")
    return any(p.endswith(s) for s in _MANIFEST_SUFFIXES)


def _pubspec_sibling_hint(requested_path: str) -> Optional[str]:
    p = (requested_path or "").lower().replace("\\", "/")
    if p.endswith("pubspec.yml"):
        return "Workspace Flutter projects use pubspec.yaml (not .yml). Do not retry pubspec.yml."
    return None


def _extract_pubspec_focus(content: str, *, max_dep_lines: int = 35) -> List[str]:
    lines_out: List[str] = []
    section: Optional[str] = None
    dep_count = 0
    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^[a-zA-Z0-9_]+:\s*", line) and not line.startswith(" "):
            key = stripped.split(":", 1)[0]
            if key in ("name", "description", "version", "publish_to", "environment"):
                lines_out.append(stripped)
                section = None
                continue
            if key in ("dependencies", "dev_dependencies", "dependency_overrides"):
                section = key
                lines_out.append(stripped)
                dep_count = 0
                continue
            section = None
            continue
        if section and (line.startswith(" ") or line.startswith("\t")):
            if dep_count >= max_dep_lines:
                lines_out.append(f"  ... ({section} truncated for focus view)")
                section = None
                continue
            lines_out.append(line)
            dep_count += 1
    return lines_out


def _extract_package_json_focus(content: str) -> List[str]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    lines: List[str] = []
    for key in ("name", "version", "private"):
        if key in data:
            lines.append(f"{key}: {data[key]}")
    for block in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(block)
        if isinstance(deps, dict) and deps:
            lines.append(f"{block}:")
            for name, ver in list(deps.items())[:30]:
                lines.append(f"  {name}: {ver}")
            if len(deps) > 30:
                lines.append(f"  ... ({len(deps) - 30} more)")
    return lines


def manifest_focus_block(path: str, content: str) -> str:
    """Short digest for dependency manifest reads."""
    if not content or content.strip().startswith("Error:"):
        return ""
    p = path.lower().replace("\\", "/")
    if p.endswith(".json"):
        focus_lines = _extract_package_json_focus(content)
    elif "pubspec" in p:
        focus_lines = _extract_pubspec_focus(content)
    else:
        focus_lines = []
    if not focus_lines:
        return ""
    return "FOCUS (project / dependencies):\n" + "\n".join(focus_lines)


def read_file_observation_line(path: str, raw_output: str) -> str:
    """One-line observation summary — path-first, manifest digest when possible."""
    safe_path = (path or "?").replace("\\", "/")
    text = str(raw_output or "")
    if text.strip().startswith("Error:"):
        hint = _pubspec_sibling_hint(safe_path)
        head = text.replace("\n", " ").strip()[:220]
        return f"path={safe_path} FAIL — {head}" + (f" ({hint})" if hint else "")

    focus = manifest_focus_block(safe_path, text) if is_dependency_manifest_path(safe_path) else ""
    if focus:
        one_line = focus.replace("\n", " | ")[:380]
        return f"path={safe_path} ok — {one_line}"
    preview = text.replace("\n", " ").strip()[:280]
    return f"path={safe_path} ok — {preview}"


def format_read_file_for_llm(
    path: str,
    raw_output: str,
    *,
    agent_role: Optional[str] = None,
    task_lane: Optional[str] = None,
) -> str:
    """Prepend a clear header + manifest focus before full read_file body."""
    text = str(raw_output or "")
    safe_path = (path or "?").replace("\\", "/")
    role = (agent_role or "").strip()
    lane = (task_lane or "").strip()

    if text.strip().startswith("Error:"):
        hint = _pubspec_sibling_hint(safe_path)
        extra = f"\nHint: {hint}" if hint else ""
        return f"=== read_file FAILED: {safe_path} ===\n{text}{extra}"

    header_parts = [f"=== read_file: {safe_path} ==="]
    if role == "Product Owner" and lane == "Needs PO":
        header_parts.append(
            "PO clarification step — file content is below. "
            "Do NOT call read_file on this path again. "
            "Use it to answer the Developer, then reply with clarification JSON "
            "and update_board → In Progress."
        )
    elif is_dependency_manifest_path(safe_path):
        header_parts.append(
            "Dependency manifest — use FOCUS + body below; prefer grep for one package name "
            "instead of re-reading the whole file."
        )

    focus = manifest_focus_block(safe_path, text) if is_dependency_manifest_path(safe_path) else ""
    blocks: List[str] = ["\n".join(header_parts)]
    if focus:
        blocks.append(focus)
        blocks.append("--- full file ---")
    blocks.append(text)
    return "\n".join(blocks)
