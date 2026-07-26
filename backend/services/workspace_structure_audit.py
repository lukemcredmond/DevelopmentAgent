"""Extensible workspace structure audit for known stacks (React, Python, .NET, Unity)."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend import state

Detector = Callable[[str], Optional[Dict[str, Any]]]


def _exists(ws: str, rel: str) -> bool:
    return os.path.exists(os.path.join(ws, rel.replace("/", os.sep)))


def _any_exists(ws: str, rels: Sequence[str]) -> Optional[str]:
    for rel in rels:
        if _exists(ws, rel):
            return rel
    return None


def _read_package_json(ws: str) -> Dict[str, Any]:
    path = os.path.join(ws, "package.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _find_csproj(ws: str) -> List[str]:
    found: List[str] = []
    try:
        for root, dirs, files in os.walk(ws):
            # Skip heavy / irrelevant dirs
            dirs[:] = [
                d
                for d in dirs
                if d not in ("node_modules", ".git", "bin", "obj", "Library", "Temp")
            ]
            for fn in files:
                if fn.endswith(".csproj"):
                    rel = os.path.relpath(os.path.join(root, fn), ws).replace("\\", "/")
                    found.append(rel)
                    if len(found) >= 5:
                        return found
    except OSError:
        pass
    return found


def _has_py_sources(ws: str) -> bool:
    for candidate in ("src", "app", "."):
        base = os.path.join(ws, candidate) if candidate != "." else ws
        if not os.path.isdir(base):
            continue
        try:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__", "venv", ".venv")]
                depth = root[len(ws) :].count(os.sep)
                if depth > 3:
                    dirs.clear()
                    continue
                for fn in files:
                    if fn.endswith(".py") and fn != "__init__.py":
                        return True
                    if fn == "__init__.py" and candidate in ("src", "app"):
                        return True
        except OSError:
            continue
    return False


def detect_react_vite(ws: str) -> Optional[Dict[str, Any]]:
    pkg = _read_package_json(ws)
    if not pkg:
        return None
    deps = {}
    for key in ("dependencies", "devDependencies"):
        section = pkg.get(key) or {}
        if isinstance(section, dict):
            deps.update(section)
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    has_react = "react" in deps
    has_vite = "vite" in deps or any("vite" in str(v) for v in scripts.values())
    has_next = "next" in deps
    if not (has_react or has_vite or has_next):
        # package.json alone is not enough — avoid false positives on pure Node backends
        return None

    stack = "react_next" if has_next else ("react_vite" if has_vite or has_react else "react_vite")
    present: List[str] = ["package.json"]
    missing: List[str] = []
    warnings: List[str] = []

    if has_next:
        page = _any_exists(ws, ("app/page.tsx", "app/page.jsx", "pages/index.tsx", "pages/index.jsx"))
        if page:
            present.append(page)
        else:
            missing.append("app/page.tsx|pages/index.tsx")
    else:
        if _exists(ws, "index.html"):
            present.append("index.html")
        else:
            missing.append("index.html")
        main = _any_exists(
            ws,
            ("src/main.tsx", "src/main.jsx", "src/index.tsx", "src/index.jsx"),
        )
        if main:
            present.append(main)
        else:
            missing.append("src/main.tsx|src/index.tsx")
        app = _any_exists(ws, ("src/App.tsx", "src/App.jsx"))
        if app:
            present.append(app)
        else:
            missing.append("src/App.tsx")

    return {
        "stack": stack,
        "present": present,
        "missing": missing,
        "warnings": warnings,
        "critical": bool(missing),
    }


def detect_python(ws: str) -> Optional[Dict[str, Any]]:
    has_marker = any(
        os.path.isfile(os.path.join(ws, m))
        for m in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")
    )
    if not has_marker and not _has_py_sources(ws):
        return None
    # Prefer not to claim python when it's clearly a Unity/dotnet/react root with incidental .py
    if os.path.isdir(os.path.join(ws, "Assets")) and os.path.isfile(
        os.path.join(ws, "ProjectSettings", "ProjectVersion.txt")
    ):
        return None
    if _find_csproj(ws) and not has_marker:
        return None
    pkg = _read_package_json(ws)
    if pkg and ("react" in str(pkg.get("dependencies") or {}) or "vite" in str(pkg)):
        if not has_marker:
            return None

    present: List[str] = []
    missing: List[str] = []
    for m in ("pyproject.toml", "requirements.txt"):
        if os.path.isfile(os.path.join(ws, m)):
            present.append(m)
    if not present and has_marker:
        for m in ("setup.py", "Pipfile"):
            if os.path.isfile(os.path.join(ws, m)):
                present.append(m)
                break
    if not present:
        missing.append("pyproject.toml|requirements.txt")

    if _has_py_sources(ws) or os.path.isdir(os.path.join(ws, "src")) or os.path.isdir(
        os.path.join(ws, "app")
    ):
        if os.path.isdir(os.path.join(ws, "src")):
            present.append("src/")
        elif os.path.isdir(os.path.join(ws, "app")):
            present.append("app/")
        else:
            present.append("(python sources)")
    else:
        missing.append("src/ or app/ python package")

    return {
        "stack": "python",
        "present": present,
        "missing": missing,
        "warnings": [],
        "critical": bool(missing),
    }


def detect_dotnet(ws: str) -> Optional[Dict[str, Any]]:
    csprojs = _find_csproj(ws)
    has_sln = any(fn.endswith(".sln") for fn in os.listdir(ws)) if os.path.isdir(ws) else False
    # Avoid treating Unity C# as a .NET SDK project
    if os.path.isdir(os.path.join(ws, "Assets")) and os.path.isfile(
        os.path.join(ws, "ProjectSettings", "ProjectVersion.txt")
    ):
        return None
    if not csprojs and not has_sln:
        return None

    present: List[str] = list(csprojs[:3])
    missing: List[str] = []
    if not csprojs:
        missing.append("*.csproj")
    else:
        # Web-ish: look for Program.cs / Startup.cs near first csproj
        first_dir = os.path.dirname(csprojs[0]) or "."
        program = _any_exists(
            ws,
            (
                f"{first_dir}/Program.cs".replace("./", ""),
                "Program.cs",
                f"{first_dir}/Startup.cs".replace("./", ""),
                "Startup.cs",
            ),
        )
        # Only require Program.cs for Web SDK projects
        try:
            csproj_path = os.path.join(ws, csprojs[0].replace("/", os.sep))
            with open(csproj_path, "r", encoding="utf-8") as f:
                text = f.read()
            is_web = "Microsoft.NET.Sdk.Web" in text
        except Exception:
            is_web = False
        if is_web:
            if program:
                present.append(program)
            else:
                missing.append("Program.cs")

    return {
        "stack": "dotnet",
        "present": present,
        "missing": missing,
        "warnings": [],
        "critical": bool(missing),
    }


def detect_unity_quest(ws: str) -> Optional[Dict[str, Any]]:
    assets = os.path.join(ws, "Assets")
    version = os.path.join(ws, "ProjectSettings", "ProjectVersion.txt")
    if not (os.path.isdir(assets) and os.path.isfile(version)):
        return None
    present = ["Assets/", "ProjectSettings/ProjectVersion.txt"]
    missing: List[str] = []
    warnings: List[str] = []
    scripts = os.path.join(ws, "Assets", "Scripts")
    if os.path.isdir(scripts):
        present.append("Assets/Scripts/")
    else:
        warnings.append("Assets/Scripts/ (recommended for Quest/VR C#)")
    return {
        "stack": "unity_quest",
        "present": present,
        "missing": missing,
        "warnings": warnings,
        "critical": False,  # Unity create-project is out of scope; warnings only
    }


def detect_unknown(ws: str) -> Optional[Dict[str, Any]]:
    return {
        "stack": "unknown",
        "present": [],
        "missing": [],
        "warnings": ["No primary stack markers detected — inventory with list_dir before feature work."],
        "critical": False,
    }


_DETECTORS: Tuple[Detector, ...] = (
    detect_unity_quest,
    detect_react_vite,
    detect_dotnet,
    detect_python,
)


def audit_workspace_structure(workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """Return structure audit for the first matching known stack (or unknown)."""
    ws = workspace_dir or state.WORKSPACE_DIR
    if not ws or not os.path.isdir(ws):
        return {
            "stack": "unknown",
            "present": [],
            "missing": [],
            "warnings": ["Workspace directory missing"],
            "critical": False,
        }
    for detector in _DETECTORS:
        result = detector(ws)
        if result:
            return result
    return detect_unknown(ws) or {
        "stack": "unknown",
        "present": [],
        "missing": [],
        "warnings": [],
        "critical": False,
    }


def structure_ok(workspace_dir: Optional[str] = None) -> bool:
    """True when no critical MISSING for a known stack (unknown always ok for gating)."""
    audit = audit_workspace_structure(workspace_dir)
    if audit.get("stack") == "unknown":
        return True
    return not bool(audit.get("critical"))


def format_structure_audit(audit: Optional[Dict[str, Any]] = None) -> str:
    """Markdown block for Dev prompts."""
    data = audit if audit is not None else audit_workspace_structure()
    stack = data.get("stack") or "unknown"
    present = data.get("present") or []
    missing = data.get("missing") or []
    warnings = data.get("warnings") or []
    lines = [
        "=== WORKSPACE STRUCTURE AUDIT ===",
        f"Stack: {stack}",
    ]
    if present:
        lines.append("Present: " + ", ".join(str(p) for p in present))
    else:
        lines.append("Present: (none)")
    if missing:
        lines.append("MISSING: " + ", ".join(str(m) for m in missing))
    if warnings:
        lines.append("Warnings: " + "; ".join(str(w) for w in warnings))
    if missing:
        lines.append(
            "Before feature work: list_dir '.', then create MISSING paths with write_file "
            "(minimal valid stubs) or rely on auto-scaffold, then implement the card AC."
        )
    elif stack == "unknown":
        lines.append("Before feature work: list_dir '.' and confirm the layout, then implement.")
    else:
        lines.append("Structure looks complete for this stack — proceed with card AC.")
    return "\n".join(lines)


def workspace_looks_empty_for_stack(audit: Dict[str, Any], workspace_dir: Optional[str] = None) -> bool:
    """Heuristic: critically incomplete / nearly empty for scaffold eligibility."""
    if not audit.get("critical"):
        return False
    ws = workspace_dir or state.WORKSPACE_DIR
    stack = audit.get("stack")
    if stack in ("react_vite", "react_next"):
        return not os.path.isdir(os.path.join(ws, "src")) and not os.path.isdir(
            os.path.join(ws, "app")
        )
    if stack == "dotnet":
        return not _find_csproj(ws)
    if stack == "python":
        return not _has_py_sources(ws) or not any(
            os.path.isfile(os.path.join(ws, m))
            for m in ("pyproject.toml", "requirements.txt")
        )
    return False
