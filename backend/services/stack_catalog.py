"""Stack-oriented tool and command reference catalog."""

from __future__ import annotations

from typing import Any, Dict, List

from backend import state
from backend.agents.registry import AGENT_MAP, agent_cr, agent_dev, agent_po, agent_qa
from backend.services.skill_suggestions import CATEGORY_LABELS, extract_brief_categories

_STACK_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "csharp",
        "label": "C# / .NET",
        "description": "ASP.NET Core APIs, console apps, xUnit/NUnit tests via dotnet CLI.",
        "recommendedSkills": ["csharp_api.md"],
        "exampleCommands": ["dotnet new webapi", "dotnet build", "dotnet test", "dotnet run"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "Install .NET SDK on the host running the backend. Use run_command for all dotnet operations.",
    },
    {
        "id": "flutter",
        "label": "Flutter / Dart",
        "description": "Cross-platform mobile and desktop with pubspec.yaml at project root.",
        "recommendedSkills": [],
        "exampleCommands": ["flutter pub get", "flutter analyze", "flutter test", "flutter run"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "Flutter SDK must be on PATH. QA auto-runs flutter analyze when pubspec.yaml exists.",
    },
    {
        "id": "web",
        "label": "Web (React / Vue / Angular)",
        "description": "Node/npm frontends and REST APIs.",
        "recommendedSkills": ["javascript_optimizer.md"],
        "exampleCommands": ["npm install", "npm run lint", "npm test", "npm run build"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "Agents use read_file, apply_patch, grep, and run_command for npm scripts.",
    },
    {
        "id": "javascript",
        "label": "JavaScript / TypeScript",
        "description": "Node backends and TS/React codebases.",
        "recommendedSkills": ["javascript_optimizer.md"],
        "exampleCommands": ["npm test", "npx tsc --noEmit", "node script.js"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "package.json scripts are detected for npm test lint heuristics.",
    },
    {
        "id": "python",
        "label": "Python",
        "description": "Scripts, FastAPI/Django backends, pytest.",
        "recommendedSkills": ["python_tester.md"],
        "exampleCommands": ["python -m pytest -q", "ruff check .", "pip install -r requirements.txt"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "run_test tries python and pytest on .py files.",
    },
    {
        "id": "android",
        "label": "Android",
        "description": "Gradle-based Android apps (often paired with Flutter or React Native).",
        "recommendedSkills": [],
        "exampleCommands": ["./gradlew assembleDebug", "./gradlew test", "adb devices"],
        "agentsWithTools": ["dev", "qa"],
        "notes": "Requires Android SDK and Gradle wrapper in workspace. Use run_command with documented paths.",
    },
    {
        "id": "vr",
        "label": "Unity / Quest 3 VR",
        "description": "Unity C# scripts, XR Interaction Toolkit, Quest Android builds.",
        "recommendedSkills": ["unity_quest_vr.md"],
        "exampleCommands": [
            "Unity -batchmode -quit -projectPath . -runTests",
            "adb install -r build.apk",
        ],
        "agentsWithTools": ["dev", "qa"],
        "notes": "Set Unity editor path in Project Memory. Library/ and Temp/ are skipped by semantic index.",
    },
]

_AGENT_REGISTRY = {
    "po": agent_po,
    "dev": agent_dev,
    "cr": agent_cr,
    "qa": agent_qa,
}


def _tools_for_agent(agent_key: str) -> List[str]:
    agent = _AGENT_REGISTRY.get(agent_key)
    if not agent:
        return []
    return sorted(agent.registry.tool_names())


def build_stack_catalog(*, use_brief: bool = True) -> Dict[str, Any]:
    """Return stack reference entries, optionally sorted by brief categories."""
    brief = state.PROJECT_BRIEF or ""
    categories = set(extract_brief_categories(brief)) if use_brief and brief.strip() else set()

    stacks: List[Dict[str, Any]] = []
    for entry in _STACK_CATALOG:
        stack = dict(entry)
        stack["tools"] = {
            agent_key: _tools_for_agent(agent_key)
            for agent_key in stack.get("agentsWithTools", [])
        }
        stack["matched"] = bool(categories.intersection({stack["id"], "web", "javascript"})) or (
            stack["id"] in categories
        )
        stacks.append(stack)

    if categories:
        stacks.sort(key=lambda s: (0 if s.get("matched") else 1, s.get("label", "")))
    else:
        stacks.sort(key=lambda s: s.get("label", ""))

    brief_categories = [
        {"id": cat_id, "label": CATEGORY_LABELS.get(cat_id, cat_id.title())}
        for cat_id in sorted(categories)
    ]

    return {
        "stacks": stacks,
        "briefCategories": brief_categories,
        "agents": list(AGENT_MAP.keys()),
    }
