"""Dev Explore → Patch → Verify phase graph with hard per-phase tool budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

CYCLE_HISTORY_MAX = 5

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

DONE_STATUS = (
    "Verify budget finished for this step (not board Done). "
    "Another step may restart at Explore if the card stays In Progress."
)

PhaseName = str  # explore | patch | verify | stuck | done


def phase_tag_for_tool(tool_name: Optional[str]) -> Optional[str]:
    """Classify a tool as explore | patch | verify for Flow tags (taxonomy fallback)."""
    name = str(tool_name or "").strip()
    if not name:
        return None
    if name in EXPLORE_TOOLS:
        return "explore"
    if name in WRITE_TOOLS:
        return "patch"
    if name in VERIFY_TOOLS:
        return "verify"
    return None


def live_phase_stamp(*, tool_name: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Stamp from active agent run phase graph; tag falls back to tool taxonomy."""
    from backend.agents.agent_run import get_active_run

    run = get_active_run()
    label: Optional[str] = None
    phase: Optional[str] = None
    if run is not None:
        label = getattr(run, "dev_phase", None)
        snap = getattr(run, "dev_phase_graph", None) or {}
        if isinstance(snap, dict):
            phase = str(snap.get("phase") or "") or None
            label = label or (str(snap.get("label")) if snap.get("label") else None)
    tag: Optional[str] = None
    if phase in ("explore", "patch", "verify"):
        tag = phase
    else:
        tag = phase_tag_for_tool(tool_name)
    if label is None and tag:
        label = tag.capitalize()
    return {"devPhase": label, "devPhaseTag": tag}


def _prior_phase_summary(prior: Optional[Dict[str, Any]]) -> str:
    if not prior or not isinstance(prior, dict):
        return "unknown"
    phase = str(prior.get("phase") or "").lower()
    if phase == "done":
        return "Verify Done"
    if phase == "stuck":
        return "Stuck"
    if phase == "verify":
        return f"Verify {prior.get('verifyCount', 0)}/{prior.get('verifyMax', 2)}"
    if phase == "patch":
        return f"Patch {prior.get('patchCount', 0)}/{prior.get('patchMax', 4)}"
    if phase == "explore":
        return f"Explore {prior.get('exploreCount', 0)}/{prior.get('exploreMax', 3)}"
    return phase.capitalize() or "unknown"


def compute_cycle_from_prior(
    *,
    prior_snap: Optional[Dict[str, Any]] = None,
    steps_on_card: int = 0,
) -> int:
    """1-based cycle index for a new Developer step on the same card."""
    if prior_snap and isinstance(prior_snap, dict):
        prior_cycle = int(prior_snap.get("cycle") or 1)
        return max(prior_cycle + 1, int(steps_on_card or 0) + 1, 2)
    if int(steps_on_card or 0) > 0:
        return int(steps_on_card) + 1
    return 1


def summarize_cycle_entry(snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Compact finished/abandoned cycle for unrolled path history."""
    if not snap or not isinstance(snap, dict) or not snap.get("phase"):
        return None
    phase = str(snap.get("phase") or "").lower()
    cycle = int(snap.get("cycle") or 1)
    step_label = str(snap.get("stepLabel") or snap.get("step_label") or f"Cycle {cycle}").strip()
    prior = str(snap.get("priorSummary") or snap.get("prior_summary") or "").strip()
    return {
        "cycle": cycle,
        "stepLabel": step_label or f"Cycle {cycle}",
        "terminalPhase": phase,
        "priorSummary": prior,
        "exploreCount": int(snap.get("exploreCount") or snap.get("explore_count") or 0),
        "patchCount": int(snap.get("patchCount") or snap.get("patch_count") or 0),
        "verifyCount": int(snap.get("verifyCount") or snap.get("verify_count") or 0),
        "writeSucceeded": bool(snap.get("writeSucceeded") or snap.get("write_succeeded")),
    }


def merge_cycle_history(
    prior_snap: Optional[Dict[str, Any]],
    *,
    max_entries: int = CYCLE_HISTORY_MAX,
) -> List[Dict[str, Any]]:
    """Carry forward prior history and append a summary of the prior cycle."""
    history: List[Dict[str, Any]] = []
    if prior_snap and isinstance(prior_snap, dict):
        raw = prior_snap.get("cycleHistory") or prior_snap.get("cycle_history") or []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                terminal = str(item.get("terminalPhase") or item.get("terminal_phase") or "").lower()
                if not terminal:
                    continue
                history.append(
                    {
                        "cycle": int(item.get("cycle") or 0) or 1,
                        "stepLabel": str(
                            item.get("stepLabel") or item.get("step_label") or f"Cycle {item.get('cycle') or 1}"
                        ),
                        "terminalPhase": terminal,
                        "priorSummary": str(item.get("priorSummary") or item.get("prior_summary") or ""),
                        "exploreCount": int(item.get("exploreCount") or item.get("explore_count") or 0),
                        "patchCount": int(item.get("patchCount") or item.get("patch_count") or 0),
                        "verifyCount": int(item.get("verifyCount") or item.get("verify_count") or 0),
                        "writeSucceeded": bool(item.get("writeSucceeded") or item.get("write_succeeded")),
                    }
                )
        summary = summarize_cycle_entry(prior_snap)
        if summary:
            # Avoid duplicating if prior snap was already recorded as last history entry
            last = history[-1] if history else None
            if not last or int(last.get("cycle") or 0) != int(summary["cycle"]):
                history.append(summary)
            elif last and last.get("terminalPhase") != summary["terminalPhase"]:
                history[-1] = summary
    if len(history) > max_entries:
        history = history[-max_entries:]
    return history


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
    cycle: int = 1
    status_text: str = ""
    step_label: str = ""
    prior_summary: str = ""
    cycle_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.step_label:
            self.step_label = f"Cycle {int(self.cycle or 1)}"
        if not self.status_text:
            self.refresh_status_text()

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

    @classmethod
    def for_new_step(
        cls,
        ws: Optional[Dict[str, Any]] = None,
        *,
        prior_snap: Optional[Dict[str, Any]] = None,
        steps_on_card: int = 0,
        focus_ac_index: Optional[int] = None,
    ) -> Optional["DevPhaseGraph"]:
        """Create a graph for a new Developer step, seeding cycle + restart status text."""
        g = cls.from_settings(ws)
        if g is None:
            return None
        g.seed_step_context(
            prior_snap=prior_snap,
            steps_on_card=steps_on_card,
            focus_ac_index=focus_ac_index,
        )
        return g

    def seed_step_context(
        self,
        *,
        prior_snap: Optional[Dict[str, Any]] = None,
        steps_on_card: int = 0,
        focus_ac_index: Optional[int] = None,
    ) -> None:
        """Set cycle, stepLabel, priorSummary, history, and initial statusText for a new step."""
        self.cycle = compute_cycle_from_prior(prior_snap=prior_snap, steps_on_card=steps_on_card)
        self.prior_summary = ""
        self.cycle_history = merge_cycle_history(prior_snap)
        label = f"Cycle {self.cycle}"
        if focus_ac_index is not None:
            try:
                ac_n = int(focus_ac_index) + 1
                if ac_n > 0:
                    label = f"{label} · AC {ac_n}"
            except (TypeError, ValueError):
                pass
        self.step_label = label

        if prior_snap and isinstance(prior_snap, dict) and prior_snap.get("phase"):
            prev = _prior_phase_summary(prior_snap)
            self.prior_summary = prev
            self.status_text = (
                f"New Developer step on this card — Explore→Patch→Verify budgets reset "
                f"(previous step: {prev}). Card is still In Progress."
            )
        elif self.cycle > 1:
            self.status_text = (
                f"New Developer step (cycle {self.cycle}) — Explore→Patch→Verify budgets reset. "
                "Card is still In Progress."
            )
        else:
            self.status_text = (
                "Step cycle 1 — reading context (Explore budget). "
                "Done here means this step's verify budget, not card Done."
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

    def refresh_status_text(self, *, stuck_message: Optional[str] = None) -> None:
        """Update statusText from current phase / counts (preserves restart text until progress)."""
        if self.phase == "done":
            self.status_text = DONE_STATUS
            return
        if self.phase == "stuck":
            if stuck_message:
                # One short line for the UI
                msg = stuck_message.strip().split("\n")[0]
                if msg.lower().startswith("stopped:"):
                    msg = msg[8:].strip()
                self.status_text = msg[:220] if msg else "Stuck — phase budget exhausted."
            elif not self.status_text:
                self.status_text = "Stuck — phase budget exhausted."
            return
        if self.phase == "explore":
            # Keep restart / first-cycle seed until explore tools actually run
            if self.explore_count == 0 and self.status_text:
                return
            left = max(0, self.explore_max - self.explore_count)
            self.status_text = (
                f"Exploring codebase ({self.explore_count}/{self.explore_max} tools; {left} left). "
                "Next: apply_patch/write_file when you have enough context."
            )
            return
        if self.phase == "patch":
            left = max(0, self.patch_max - self.patch_count)
            self.status_text = (
                f"Patching ({self.patch_count}/{self.patch_max} write attempts; {left} left). "
                "Need a successful apply_patch/write_file to enter Verify."
            )
            return
        if self.phase == "verify":
            left = max(0, self.verify_max - self.verify_count)
            self.status_text = (
                f"Verifying after write ({self.verify_count}/{self.verify_max} tools; {left} left). "
                "Done means this step's verify budget, not board Done."
            )
            return
        if not self.status_text:
            self.status_text = self.label()

    def snapshot(self) -> Dict[str, Any]:
        if not self.step_label:
            self.step_label = f"Cycle {int(self.cycle or 1)}"
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
            "cycle": int(self.cycle or 1),
            "statusText": self.status_text or "",
            "stepLabel": self.step_label or f"Cycle {int(self.cycle or 1)}",
            "priorSummary": self.prior_summary or "",
            "cycleHistory": list(self.cycle_history or [])[-CYCLE_HISTORY_MAX:],
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

        self.refresh_status_text()
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
        self.refresh_status_text(stuck_message=message)
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
