import os
from typing import Dict, List

from backend import state

_DEFAULT_SKILL_TEMPLATES = {
    "git_expert.md": "# Git Expert Skill\nAlways commit changes using clean semantic messages. Check file diffs carefully.",
    "python_tester.md": "# Python Unit Tester Skill\nEnsure code has unittest coverage checking for negative and overflow bounds.",
    "javascript_optimizer.md": "# ES6 JS Optimization Skill\nWrite code utilizing modular functions, arrow notations, and clean error captures.",
    "acceptance_tester.md": "# Dynamic QA Acceptance Skill\nValidate user workflows match exact brief expectations. Write automated check reports.",
    "code_auditor.md": "# Code Reviewer Auditor Skill\nVerify architecture patterns, import structures, syntax errors, and complexity levels.",
    "csharp_api.md": (
        "# C# / .NET Application Skill\n"
        "Target ASP.NET Core or console apps with modern C# (10+). Use `dotnet build` and `dotnet test` via run_command. "
        "Prefer xUnit or NUnit for tests; keep Program.cs minimal with DI. "
        "Structure: Controllers/Services/Models for APIs; appsettings.json for config (never commit secrets). "
        "Use async/await for I/O; validate inputs with DataAnnotations or FluentValidation. "
        "Pin framework version and test approach in the project brief."
    ),
    "unity_quest_vr.md": (
        "# Unity Quest 3 VR Skill\n"
        "Edit C# scripts under Assets/; avoid modifying Library/ or Temp/. "
        "Use XR Interaction Toolkit or Meta XR SDK per project brief. Target Android/Quest builds. "
        "Run tests via run_command (Unity batchmode -runTests or dotnet test for edit-mode tests). "
        "Document build commands in Project Memory (Unity path, scene names, package versions). "
        "Keep MonoBehaviour scripts focused; use ScriptableObjects for data. "
        "Quest deploy requires Android build + adb/Meta tooling — script these in run_command when paths are known."
    ),
}


def _ensure_skill_templates() -> None:
    """Create missing default skill files without overwriting existing ones."""
    try:
        os.makedirs(state.SKILLS_DIR, exist_ok=True)
        for name, content in _DEFAULT_SKILL_TEMPLATES.items():
            path = os.path.join(state.SKILLS_DIR, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
    except Exception:
        pass


def scan_skills_directory() -> List[Dict[str, str]]:
    """Recursively scans SKILLS_DIR for markdown or text skill files."""
    skills: List[Dict[str, str]] = []
    _ensure_skill_templates()

    if os.path.exists(state.SKILLS_DIR):
        for root, _dirs, files in os.walk(state.SKILLS_DIR):
            for file in files:
                if file.endswith((".md", ".txt")):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, state.SKILLS_DIR).replace("\\", "/")
                    folder = os.path.dirname(rel_path).replace("\\", "/")
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            preview = f.readline().strip().replace("#", "").strip()
                        skills.append(
                            {
                                "filename": rel_path,
                                "title": preview if preview else file,
                                "folder": folder if folder else ".",
                            }
                        )
                    except Exception:
                        pass
    skills.sort(key=lambda s: s["filename"].lower())
    return skills
