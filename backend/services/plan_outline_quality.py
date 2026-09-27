"""Deterministic quality checks for markdown project plan outlines."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend import state

_REQUIRED_HEADERS = ("summary", "approach", "proposed epics")
_RECOMMENDED_HEADERS = (
    "data model",
    "flows",
    "errors",
    "testing",
    "epic details",
    "risks",
    "open questions",
)


@dataclass
class PlanOutlineIssue:
    code: str
    severity: str  # fail | warn
    message: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class PlanOutlineValidation:
    ok: bool
    score: int
    issues: List[PlanOutlineIssue] = field(default_factory=list)
    sections: Dict[str, bool] = field(default_factory=dict)
    epic_bullet_count: int = 0
    epic_detail_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score,
            "issues": [i.to_dict() for i in self.issues],
            "sections": self.sections,
            "epicBulletCount": self.epic_bullet_count,
            "epicDetailCount": self.epic_detail_count,
        }


def sample_v2_plan_outline(summary: str = "Offline meal app.") -> str:
    """Minimal valid v2 outline for tests and offline stubs."""
    epics = "\n".join(
        f"- Epic {i} — capability {i} because users need it"
        for i in range(1, 7)
    )
    details = "\n\n".join(
        f"### Epic: Epic {i}\n"
        f"- **Scope**\n  - Do thing {i}\n"
        f"- **Suggested child cards**\n  - Child A\n  - Child B\n  - Child C\n"
        f"- **AC hints**\n  - User can complete flow {i}\n"
        for i in range(1, 7)
    )
    return f"""## Summary
{summary}

## Approach
Flutter offline-first.

## Data model
MealDay entity.

## Flows
Import → shop.

## Errors and edge cases
INVALID_JSON — readable message.

## Testing
flutter test

## Risks
Scope creep.

## Open questions
None.

## Proposed epics
{epics}

## Epic details
{details}
"""


def normalize_pasted_plan(text: str) -> str:
    """Strip chat preamble before the first real plan heading."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    plan_match = re.search(r"(?im)^#\s*plan\b.*$", raw)
    if plan_match:
        return raw[plan_match.start() :].lstrip()
    summary_match = re.search(r"(?im)^##\s*summary\s*$", raw)
    if summary_match:
        return raw[summary_match.start() :].lstrip()
    return raw


def _looks_like_json_payload(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return False
    try:
        parsed = json.loads(stripped)
        return parsed is not None
    except json.JSONDecodeError:
        return False


def _header_present(lower_text: str, name: str) -> bool:
    pattern = rf"(?m)^##\s*{re.escape(name)}\s*$"
    if re.search(pattern, lower_text, re.IGNORECASE):
        return True
    # Flexible match for "Errors and edge cases" etc.
    if name == "errors":
        return bool(re.search(r"(?m)^##\s*errors\b", lower_text, re.IGNORECASE))
    if name == "epic details":
        return bool(re.search(r"(?m)^##\s*epic\s+details\s*$", lower_text, re.IGNORECASE))
    return False


def _parse_proposed_epic_bullets(outline: str) -> List[str]:
    from backend.services.feature_service import _parse_proposed_epic_bullets

    return _parse_proposed_epic_bullets(outline)


def _count_epic_detail_blocks(outline: str) -> int:
    return len(re.findall(r"(?im)^###\s*epic\s*:\s*.+$", outline or ""))


def _min_epic_bullets_for_brief(brief_len: int) -> int:
    if brief_len <= 200:
        return 2
    if brief_len <= 400:
        return 4
    return 6


def _min_outline_chars_for_brief(brief_len: int) -> int:
    if brief_len <= 400:
        return 200
    return 800


def validate_plan_outline(
    text: Optional[str],
    *,
    brief: Optional[str] = None,
    strict: bool = True,
) -> PlanOutlineValidation:
    """Score and validate a markdown plan outline (deterministic)."""
    raw = str(text or "").strip()
    issues: List[PlanOutlineIssue] = []
    score = 100
    sections: Dict[str, bool] = {}

    if not raw:
        return PlanOutlineValidation(
            ok=False,
            score=0,
            issues=[
                PlanOutlineIssue("empty", "fail", "Plan outline is empty."),
            ],
            sections=sections,
        )

    if _looks_like_json_payload(raw):
        issues.append(
            PlanOutlineIssue(
                "json_not_markdown",
                "fail",
                "Plan must be markdown, not JSON (task-spec echo or epics JSON).",
            )
        )
        score -= 80

    if looks_like_raw_tool_markup(raw):
        issues.append(
            PlanOutlineIssue(
                "tool_markup",
                "fail",
                "Plan contains tool-call markup instead of markdown.",
            )
        )
        score -= 80

    lower = raw.lower()
    for hdr in _REQUIRED_HEADERS:
        present = _header_present(lower, hdr)
        sections[hdr] = present
        if not present:
            issues.append(
                PlanOutlineIssue(
                    f"missing_{hdr.replace(' ', '_')}",
                    "fail",
                    f'Missing required section "## {hdr.title()}" (or equivalent).',
                )
            )
            score -= 25

    for hdr in _RECOMMENDED_HEADERS:
        present = _header_present(lower, hdr)
        sections[hdr] = present
        if not present:
            issues.append(
                PlanOutlineIssue(
                    f"missing_recommended_{hdr.replace(' ', '_')}",
                    "warn",
                    f'Recommended section "## {hdr.title()}" is missing.',
                )
            )
            score -= 5

    epic_bullets = _parse_proposed_epic_bullets(raw)
    epic_detail_count = _count_epic_detail_blocks(raw)
    brief_text = brief if brief is not None else str(getattr(state, "PROJECT_BRIEF", "") or "")
    brief_len = len(brief_text.strip())
    min_epics = _min_epic_bullets_for_brief(brief_len)
    min_chars = _min_outline_chars_for_brief(brief_len)

    if len(epic_bullets) < min_epics:
        issues.append(
            PlanOutlineIssue(
                "too_few_epics",
                "fail" if strict else "warn",
                f"Proposed epics has {len(epic_bullets)} bullet(s); need at least {min_epics} "
                f"for this brief length.",
            )
        )
        score -= 20

    if len(raw) < min_chars and brief_len > 400:
        issues.append(
            PlanOutlineIssue(
                "outline_too_short",
                "fail" if strict else "warn",
                f"Plan is {len(raw)} chars; expected at least {min_chars} for a detailed brief.",
            )
        )
        score -= 15

    thin_epics = [
        b for b in epic_bullets if len(b) < 20 or not re.search(r"[—–\-:]", b)
    ]
    if epic_bullets and len(thin_epics) > len(epic_bullets) // 2:
        issues.append(
            PlanOutlineIssue(
                "thin_epic_bullets",
                "warn",
                "Many Proposed epics bullets lack detail (use “Title — capability + why”).",
            )
        )
        score -= 10

    if epic_bullets and epic_detail_count == 0:
        issues.append(
            PlanOutlineIssue(
                "no_epic_details",
                "warn",
                'Missing "## Epic details" with ### Epic: … subsections.',
            )
        )
        score -= 15
    elif epic_bullets and epic_detail_count < min(len(epic_bullets), min_epics) // 2:
        issues.append(
            PlanOutlineIssue(
                "sparse_epic_details",
                "warn",
                f"Only {epic_detail_count} epic detail block(s) for {len(epic_bullets)} proposed epic(s).",
            )
        )
        score -= 8

    fails = [i for i in issues if i.severity == "fail"]
    ok = len(fails) == 0
    if not strict and fails:
        # User-edit escape: relax epic count / length only — keep required sections.
        relaxed = {"too_few_epics", "outline_too_short"}
        hard = [i for i in fails if i.code not in relaxed]
        ok = len(hard) == 0

    score = max(0, min(100, score))
    return PlanOutlineValidation(
        ok=ok,
        score=score,
        issues=issues,
        sections=sections,
        epic_bullet_count=len(epic_bullets),
        epic_detail_count=epic_detail_count,
    )


def looks_like_markdown_plan_attempt(text: Optional[str]) -> bool:
    """True when text looks like a markdown plan (not epics JSON), even if rubric fails."""
    raw = str(text or "").strip()
    if not raw or _looks_like_json_payload(raw):
        return False
    lower = raw.lower()
    return "## summary" in lower or raw.startswith("##")


def looks_like_raw_tool_markup(content: str) -> bool:
    from backend.services.llm_tool_recovery import looks_like_raw_tool_markup as _inner

    return _inner(content)


def brief_epic_coverage_warnings(brief: str, epic_titles: List[str]) -> List[str]:
    """Light heuristic: epic titles should mention words from the brief."""
    words = {
        w.lower()
        for w in re.findall(r"[a-zA-Z]{4,}", brief or "")
        if w.lower() not in {"with", "that", "this", "from", "have", "will", "must", "should"}
    }
    if not words or not epic_titles:
        return []
    warnings: List[str] = []
    for title in epic_titles:
        title_words = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", title)}
        if title_words and not (title_words & words):
            warnings.append(f"Epic '{title}' has little overlap with brief keywords.")
    return warnings
