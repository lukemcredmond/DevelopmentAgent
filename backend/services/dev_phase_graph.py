"""Dev Explore → Patch → Verify phase graph with hard per-phase tool budgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXPLORE_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "glob_file_search",
        "list_dir",
        "search_code",
        "semantic_search",
        "graph_query",
    }
)
WRITE_TOOLS = frozenset({"apply_patch", "write_file"})
VERIFY_TOOLS = frozenset({"run_command", "run_test"})

EXPLORE_NUDGE = (
    "=== DEV PHASE: EXPLORE BUDGET REACHED ===\n"
    "You already have enough context from explore tools. "
    "Do NOT call more read_file/grep/list_dir. "
    "Next message MUST call apply_patch or write_file for this card's acceptance criteria."
)

PhaseName = str  # explore | patch | verify | stuck | done


@dataclass
class PhaseAction:
    """Result of recording tools / checking after an LLM turn."""

    nudge: Optional[str] = None
    stop_reason: Optional[str] = None
    stop_message: Optional[str] = None
    phase_changed: bool = False


@dataclass
class DevPhaseGraph:
    explore_max: int = 3
    patch_max: int = 4
    verify_max: int = 2
    phase: PhaseName = "explore"
    explore_count: int = 0
    patch_count: int = 0
    verify_count: int = 0
    write_succeeded: bool = False
    explore_nudge_sent: bool = False
    pending_stop_after_nudge: bool = False

    @classmethod
    def from_settings(cls, ws: Optional[Dict[str, Any]] = None) -> Optional["DevPhaseGraph"]:
        if ws is None:
            from backend.services.workflow_settings import get_workflow_settings

            ws = get_workflow_settings()
        if not bool(ws.get("enableDevPhaseGraph", True)):
            return None
        return cls(
            explore_max=max(1, int(ws.get("devExploreMaxTools") or 3)),
            patch_max=max(1, int(ws.get("devPatchMaxTools") or 4)),
            verify_max=max(1, int(ws.get("devVerifyMaxTools") or 2)),
        )

    @staticmethod
    def applies_to(*, role: str, lane: Optional[str]) -> bool:
        return role == "Developer" and lane == "In Progress"

    def label(self) -> str:
        if self.phase == "explore":
            return f"Explore {self.explore_count}/{self.explore_max}"
        if self.phase == "patch":
            return f"Patch {self.patch_count}/{self.patch_max}"
        if self.phase == "verify":
            return f"Verify {self.verify_count}/{self.verify_max}"
        return self.phase.capitalize()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "label": self.label(),
            "exploreCount": self.explore_count,
            "exploreMax": self.explore_max,
            "patchCount": self.patch_count,
            "patchMax": self.patch_max,
            "verifyCount": self.verify_count,
            "verifyMax": self.verify_max,
            "writeSucceeded": self.write_succeeded,
        }

    def record_batch(self, tools: Sequence[Tuple[str, bool]]) -> PhaseAction:
        """Record (tool_name, success) pairs from one tool batch."""
        if self.phase in ("stuck", "done"):
            return PhaseAction()

        prev = self.phase
        action = PhaseAction()
        saw_write_attempt = False
        saw_write_success = False
        saw_verify = False

        for name, success in tools:
            if name in WRITE_TOOLS:
                saw_write_attempt = True
                self.patch_count += 1
                if success:
                    saw_write_success = True
                    self.write_succeeded = True
            elif name in VERIFY_TOOLS:
                if self.write_succeeded or self.phase == "verify":
                    saw_verify = True
                    self.verify_count += 1
                elif self.phase == "explore":
                    # Verify tools before any write still count as explore thrash.
                    self.explore_count += 1
            elif name in EXPLORE_TOOLS:
                if self.phase == "explore":
                    self.explore_count += 1
                elif self.phase == "patch" and not self.write_succeeded:
                    # Extra reads while trying to patch — still bill against patch budget lightly
                    # by not incrementing explore; allow them but patch budget is what stops.
                    pass
                elif self.phase == "verify":
                    pass

        if saw_write_success:
            self.phase = "verify"
            self.pending_stop_after_nudge = False
        elif saw_write_attempt and self.phase == "explore":
            self.phase = "patch"
        elif saw_verify and self.write_succeeded:
            self.phase = "verify"

        # Explore budget exhausted → nudge once; a later explore-only batch stops.
        nudged_this_batch = False
        if (
            self.phase == "explore"
            and not self.write_succeeded
            and self.explore_count >= self.explore_max
        ):
            if not self.explore_nudge_sent:
                self.explore_nudge_sent = True
                self.pending_stop_after_nudge = True
                action.nudge = EXPLORE_NUDGE
                nudged_this_batch = True
            else:
                return self._stuck(
                    "explore_budget_exhausted",
                    "Stopped: explore tool budget reached without apply_patch/write_file. "
                    "Split the card or narrow AC, then Run In Progress.",
                )

        if self.phase == "patch" and not self.write_succeeded and self.patch_count >= self.patch_max:
            return self._stuck(
                "patch_budget_exhausted",
                "Stopped: patch tool budget reached without a successful write. "
                "Check apply_patch args or Split the card.",
            )

        if self.phase == "verify" and self.write_succeeded and self.verify_count >= self.verify_max:
            self.phase = "done"

        # After an earlier nudge, another explore-only batch without write → stop
        if (
            not nudged_this_batch
            and self.pending_stop_after_nudge
            and not self.write_succeeded
            and self.explore_nudge_sent
            and tools
            and not any(n in WRITE_TOOLS for n, _ in tools)
            and any(n in EXPLORE_TOOLS for n, _ in tools)
        ):
            return self._stuck(
                "explore_budget_exhausted",
                "Stopped: explore tool budget reached without apply_patch/write_file. "
                "Split the card or narrow AC, then Run In Progress.",
            )

        action.phase_changed = prev != self.phase
        return action

    def after_llm_turn_without_write(self) -> PhaseAction:
        """Call when an LLM turn finishes with no successful write after explore nudge."""
        if self.pending_stop_after_nudge and self.explore_nudge_sent and not self.write_succeeded:
            return self._stuck(
                "explore_budget_exhausted",
                "Stopped: explore tool budget reached without apply_patch/write_file. "
                "Split the card or narrow AC, then Run In Progress.",
            )
        return PhaseAction()

    def _stuck(self, reason: str, message: str) -> PhaseAction:
        self.phase = "stuck"
        return PhaseAction(stop_reason=reason, stop_message=message)


def hint_for_exit_reason(exit_reason: str) -> Optional[str]:
    hints = {
        "explore_budget_exhausted": (
            "Spent explore budget with no edits — Split card or Run In Progress after narrowing AC."
        ),
        "patch_budget_exhausted": (
            "Patch attempts exhausted without a successful write — check tool errors or Split card."
        ),
    }
    return hints.get(exit_reason)
