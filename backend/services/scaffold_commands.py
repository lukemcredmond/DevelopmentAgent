"""Rewrite project-create CLIs so they target the workspace root (.)."""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

from backend import state

_FLUTTER_CREATE_VALUE_FLAGS = {
    "--org",
    "--project-name",
    "--platforms",
    "--ios-language",
    "--android-language",
    "--template",
    "-t",
    "--description",
    "--sample",
}

_MONOREPO_PREFIXES = ("packages/", "apps/", "package/", "app/")


def rewrite_scaffold_command(command: str) -> Tuple[str, Optional[str]]:
    """Return (command_to_run, optional_agent_note). Unchanged when not a nested create."""
    raw = str(command or "").strip()
    if not raw:
        return raw, None
    lower = raw.lower()
    if _looks_like_flutter_create(lower):
        return _rewrite_flutter_create(raw)
    if _looks_like_dotnet_new(lower):
        return _rewrite_dotnet_new(raw)
    if _looks_like_vite_create(lower):
        return _rewrite_vite_create(raw)
    return raw, None


def _tokenize(command: str) -> Optional[List[str]]:
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _is_monorepo_target(target: str) -> bool:
    norm = target.replace("\\", "/").lstrip("./")
    if "/" in norm:
        return True
    return any(norm.startswith(p) for p in _MONOREPO_PREFIXES)


def _root_has_pubspec() -> bool:
    ws = getattr(state, "WORKSPACE_DIR", None) or ""
    return bool(ws) and os.path.isfile(os.path.join(ws, "pubspec.yaml"))


def dart_package_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"app_{slug}" if slug else "app"
    return slug[:40]


def _looks_like_flutter_create(lower: str) -> bool:
    compact = " ".join(lower.replace(";", " ").replace("&", " ").split())
    return "flutter create" in compact


def _looks_like_dotnet_new(lower: str) -> bool:
    compact = " ".join(lower.replace(";", " ").replace("&", " ").split())
    return "dotnet new" in compact


def _looks_like_vite_create(lower: str) -> bool:
    return (
        "create vite" in lower
        or "init vite" in lower
        or "create-vite" in lower
        or "create vite@" in lower
    )


def _rewrite_flutter_create(command: str) -> Tuple[str, Optional[str]]:
    parts = _tokenize(command)
    if not parts:
        return command, None
    create_idx = None
    for i, tok in enumerate(parts):
        if tok.lower() != "create":
            continue
        if i == 0:
            continue
        prev = parts[i - 1].lower()
        if prev == "flutter" or prev.endswith("/flutter") or prev.endswith("\\flutter"):
            create_idx = i
            break
    if create_idx is None:
        return command, None

    positionals: List[Tuple[int, str]] = []
    has_project_name = False
    i = create_idx + 1
    while i < len(parts):
        tok = parts[i]
        low = tok.lower()
        if tok == "--":
            break
        if low == "--project-name" or low.startswith("--project-name="):
            has_project_name = True
        if tok.startswith("-"):
            key = tok.split("=", 1)[0].lower()
            if "=" in tok:
                i += 1
                continue
            if key in _FLUTTER_CREATE_VALUE_FLAGS:
                i += 2
                continue
            i += 1
            continue
        positionals.append((i, tok))
        i += 1

    if len(positionals) != 1:
        return command, None
    idx, target = positionals[0]
    if target in (".", "./"):
        return command, None
    if _is_monorepo_target(target):
        return command, None

    if _root_has_pubspec():
        note = (
            "Skipped flutter create: pubspec.yaml already exists at workspace root. "
            "The workspace directory is the app root — use paths like lib/main.dart."
        )
        return "echo skipped_flutter_create_pubspec_exists", note

    parts[idx] = "."
    if not has_project_name:
        parts.extend(["--project-name", dart_package_name(target)])
    rewritten = shlex.join(parts)
    note = (
        "Workspace is the app root; rewritten nested flutter create to "
        f"`{rewritten}`."
    )
    return rewritten, note


def _rewrite_dotnet_new(command: str) -> Tuple[str, Optional[str]]:
    parts = _tokenize(command)
    if not parts:
        return command, None
    has_output = False
    has_name = False
    for tok in parts:
        low = tok.lower()
        if low in ("-o", "--output") or low.startswith("--output=") or low.startswith("-o="):
            has_output = True
        if low in ("-n", "--name") or low.startswith("--name=") or low.startswith("-n="):
            has_name = True
    if not has_name or has_output:
        return command, None
    parts.extend(["-o", "."])
    rewritten = shlex.join(parts)
    note = f"Workspace is the app root; rewritten nested dotnet new to `{rewritten}`."
    return rewritten, note


def _rewrite_vite_create(command: str) -> Tuple[str, Optional[str]]:
    parts = _tokenize(command)
    if not parts:
        return command, None
    pkg_idx = None
    for i, tok in enumerate(parts):
        low = tok.lower()
        if "vite" in low and (
            "create" in " ".join(p.lower() for p in parts[: i + 1])
            or low.startswith("create-vite")
        ):
            pkg_idx = i
            if low.startswith("create-vite") or "vite@" in low or low.endswith("vite"):
                break
    if pkg_idx is None:
        return command, None

    name_idx = None
    i = pkg_idx + 1
    while i < len(parts):
        tok = parts[i]
        if tok == "--":
            break
        if tok.startswith("-"):
            if "=" not in tok and i + 1 < len(parts) and not parts[i + 1].startswith("-") and parts[i + 1] != "--":
                i += 2
                continue
            i += 1
            continue
        name_idx = i
        break
    if name_idx is None:
        return command, None
    target = parts[name_idx]
    if target in (".", "./"):
        return command, None
    if _is_monorepo_target(target):
        return command, None
    parts[name_idx] = "."
    rewritten = shlex.join(parts)
    note = f"Workspace is the app root; rewritten nested Vite create to `{rewritten}`."
    return rewritten, note
