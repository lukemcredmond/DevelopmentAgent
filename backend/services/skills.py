import os
from typing import Dict, List

from backend import state


def scan_skills_directory() -> List[Dict[str, str]]:
    """Recursively scans SKILLS_DIR for markdown or text skill files."""
    skills: List[Dict[str, str]] = []
    if not os.path.exists(state.SKILLS_DIR):
        try:
            os.makedirs(state.SKILLS_DIR, exist_ok=True)
            default_skills = {
                "git_expert.md": "# Git Expert Skill\nAlways commit changes using clean semantic messages. Check file diffs carefully.",
                "python_tester.md": "# Python Unit Tester Skill\nEnsure code has unittest coverage checking for negative and overflow bounds.",
                "javascript_optimizer.md": "# ES6 JS Optimization Skill\nWrite code utilizing modular functions, arrow notations, and clean error captures.",
                "acceptance_tester.md": "# Dynamic QA Acceptance Skill\nValidate user workflows match exact brief expectations. Write automated check reports.",
                "code_auditor.md": "# Code Reviewer Auditor Skill\nVerify architecture patterns, import structures, syntax errors, and complexity levels.",
            }
            for name, content in default_skills.items():
                with open(os.path.join(state.SKILLS_DIR, name), "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception:
            pass

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
