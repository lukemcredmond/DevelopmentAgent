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


def detect_flutter(ws: str) -> Optional[Dict[str, Any]]:
    nested = _nested_pubspec_dirs(ws)
    root_pub = os.path.join(ws, "pubspec.yaml")
    has_root = os.path.isfile(root_pub)
    has_lib_dart = _has_lib_dart(ws)
    if not has_root and not has_lib_dart and not nested:
        return None
    if os.path.isdir(os.path.join(ws, "Assets")) and os.path.isfile(
        os.path.join(ws, "ProjectSettings", "ProjectVersion.txt")
    ):
        return None

    present: List[str] = []
    missing: List[str] = []
    warnings: List[str] = []
    if has_root:
        present.append("pubspec.yaml")
    else:
        missing.append("pubspec.yaml")
    main = _any_exists(ws, ("lib/main.dart",))
    if main:
        present.append(main)
    elif has_lib_dart:
        present.append("lib/")
    else:
        missing.append("lib/main.dart")
    if not has_root and len(nested) == 1:
        child = nested[0]
        present.append(f"{child}/pubspec.yaml")
        warnings.append(
            f"Nested Flutter project in {child}/ — workspace root should contain pubspec.yaml. "
            "Do not scaffold into a subfolder; use flutter create ."
        )
    return {
        "stack": "flutter",
        "present": present,
        "missing": missing,
        "warnings": warnings,
        "critical": bool(missing),
    }


def _nested_pubspec_dirs(ws: str) -> List[str]:
    skip = {
        "lib",
        "src",
        "android",
        "ios",
        "macos",
        "linux",
        "windows",
        "web",
        "test",
        "tests",
        "packages",
        "apps",
        "node_modules",
        "build",
        "dist",
        "bin",
        "obj",
        "integration_test",
    }
    found: List[str] = []
    if not os.path.isdir(ws):
        return found
    try:
        for name in os.listdir(ws):
            if name.startswith(".") or name in skip:
                continue
            child = os.path.join(ws, name)
            if os.path.isdir(child) and os.path.isfile(os.path.join(child, "pubspec.yaml")):
                found.append(name)
    except OSError:
        return found
    return found


def _has_lib_dart(ws: str) -> bool:
    lib = os.path.join(ws, "lib")
    if not os.path.isdir(lib):
        return False
    try:
        for root, dirs, files in os.walk(lib):
            dirs[:] = [d for d in dirs if d not in (".dart_tool",)]
            if any(fn.endswith(".dart") for fn in files):
                return True
    except OSError:
        return False
    return False


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


_GREENFIELD_IGNORE = {
    "allhands.project.json",
    "docs",
    "skills",
    "readme",
    "readme.md",
    "license",
    "license.md",
    ".git",
    ".allhands",
    ".gitignore",
}


def workspace_is_greenfield(ws: str) -> bool:
    """True when the workspace has no app source — only project metadata / docs / skills."""
    if not ws or not os.path.isdir(ws):
        return True
    try:
        names = os.listdir(ws)
    except OSError:
        return True
    for name in names:
        if name.startswith("."):
            continue
        if name.lower() in _GREENFIELD_IGNORE:
            continue
        return False
    return True


def infer_stack_from_brief(
    brief: Optional[str] = None,
    *,
    project_name: Optional[str] = None,
    task: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Infer a stack id from brief/name when the workspace has no markers.

    Flutter is the only greenfield inference in this pass (empty repo + dart/flutter keywords).
    """
    parts = [
        str(brief if brief is not None else (getattr(state, "PROJECT_BRIEF", None) or "")),
        str(project_name if project_name is not None else (getattr(state, "PROJECT_NAME", None) or "")),
    ]
    if isinstance(task, dict):
        parts.append(str(task.get("title") or ""))
        parts.append(str(task.get("description") or ""))
    blob = " ".join(parts).strip()
    if not blob:
        return None
    try:
        from backend.services.skill_suggestions import extract_brief_categories

        cats = set(extract_brief_categories(blob))
    except Exception:
        cats = set()
        lower = blob.lower()
        if "flutter" in lower or "dart" in lower:
            cats.add("flutter")
    if "flutter" in cats:
        return "flutter"
    return None


def inferred_flutter_audit() -> Dict[str, Any]:
    return {
        "stack": "flutter",
        "present": [],
        "missing": ["pubspec.yaml", "lib/main.dart"],
        "warnings": [
            "Inferred Flutter from the project brief — workspace has no pubspec.yaml yet."
        ],
        "critical": True,
        "inferredFromBrief": True,
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
    detect_flutter,
    detect_react_vite,
    detect_dotnet,
    detect_python,
)


def audit_workspace_structure(
    workspace_dir: Optional[str] = None,
    *,
    brief: Optional[str] = None,
    task: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
    if workspace_is_greenfield(ws) and infer_stack_from_brief(brief, task=task) == "flutter":
        return inferred_flutter_audit()
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


def greenfield_wander_nudge(
    *,
    brief: Optional[str] = None,
    task: Optional[Dict[str, Any]] = None,
    workspace_dir: Optional[str] = None,
) -> str:
    """One-line Dev instruction when a greenfield Flutter repo has no Dart sources yet."""
    ws = workspace_dir or state.WORKSPACE_DIR
    if not workspace_is_greenfield(ws):
        return ""
    if infer_stack_from_brief(brief, task=task) != "flutter":
        return ""
    import glob

    if glob.glob(os.path.join(ws, "**", "*.dart"), recursive=True):
        return ""
    return (
        "=== GREENFIELD DEV NOTE ===\n"
        "Do not re-list docs/ or docs/tasks/ — scaffold or patch lib/ now "
        "(apply_patch/write_file)."
    )


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
    if stack == "flutter":
        return not os.path.isfile(os.path.join(ws, "pubspec.yaml"))
    return False
