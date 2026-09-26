---
name: Cursor-like Diagnostics Improvements
overview: Review of 51 post-fix step diagnostics (12 cards, drift/grocery sprint) shows prior runaway/patch fixes worked (avg 115s vs 23min), but the sprint now fails Cursor-style on text apology loops (18/51 steps) and bloated prompts—not patch mismatches. Plan targets tool-required agent behavior, prompt diet, and honest latch UX.
todos:
  - id: p0-text-loop-reorder
    content: "Reorder text reject flow: forced tool mode on 1st reject, backup model on apology, Needs User before text_rejection_loop hard stop"
    status: completed
  - id: p0-tool-required-fallback
    content: When forcedToolMode still gets text-only, synthesize read_file on target path or park to Needs User
    status: completed
  - id: p0-latch-ux-hints
    content: Fix _outcome_suggested_action and step hints for phase_cycle_cap and text_rejection_loop; auto-park latched cards
    status: completed
  - id: p1-prompt-diet
    content: Slim forced-patch/lint-wall prompts; cap PATCH RECOVERY/REFLECT injects; 70% num_ctx prompt budget guard
    status: completed
  - id: p1-bypass-explore-budget
    content: Skip explore phase budgets on forcePatch/lint-wall steps; read+write cadence only
    status: completed
  - id: p2-native-tool-probe
    content: Step-start tool probe with backup model routing when native tool calls fail
    status: completed
  - id: p2-cursor-diagnostics
    content: Add nativeToolCallRate, promptTokensAtFirstCall, cursorLikeness fields to diagnostics + TaskDetailModal
    status: completed
  - id: validate-cursor-batch
    content: Re-run TASK-971, TASK-3FD5, TASK-BD3E1823; verify no text loops and promptTokens<8k on lint cards
    status: completed
isProject: false
---

# Cursor-like improvements from latest diagnostics batch

## What changed since the prior fix batch

The [prior diagnostics plan](file:///home/lukemcredmond/.cursor/plans/diagnostics_batch_review_797ae04f.plan.md) items are **landed** (`runawayAbortCapPerStep`, `cardToolFailures`, noop patch reject, fix-verify lint skip, etc.). Comparing batches:

| Metric | Prior batch (48 steps) | Latest batch (51 steps) |
|---|---|---|
| Avg step duration | ~23 min | **~2 min** |
| Dominant exit | `explore_budget_exhausted` (11) | **`text_rejection_loop` (18)** |
| `apply_patch` failures | 28 | **13** (down) |
| Runaway steps | 6 (882s worst) | 6 (347s max) |
| `cardToolFailures` in traces | 0/48 | **50/51** |
| Cards reaching Done/QA | 0 | **1** (`Implement drift database schema`) |

**Conclusion:** runaway retry cap and patch hardening helped. The sprint now fails in a way that is **unlike Cursor**: the model apologizes in prose instead of calling tools, often after reading a 15–20k-token prompt full of recovery/latch context.

---

## Latest batch failure taxonomy

```mermaid
flowchart TD
  subgraph cursorGap [Cursor gap: text-first model]
    DevStep -->|"14-20k token prompt"| Ollama
    Ollama -->|"I'm sorry, I can't..."| TextReject
    TextReject -->|"identical x2"| TextLoop[text_rejection_loop]
    TextLoop --> StayIP[Stays In Progress]
  end
  subgraph working [Working Cursor-like pieces]
    ParallelReads[parallel read_file/grep] --> PatchOrWrite
    PatchOrWrite --> FixVerify
    FixVerify -->|"skipped_lint_no_writes"| FastFail
  end
  subgraph latchUX [Broken Cursor-like UX]
    PhaseCap[phase_cycle_cap latched] --> BadHint["suggestedAction: Run In Progress again"]
  end
```

| Exit reason | Count | Cursor-like? |
|---|---|---|
| `text_rejection_loop` | 18 | No — Cursor never accepts apology text as a step outcome |
| `llm_call_failed` | 6 | Partial — fast fail is good; should pivot model |
| `phase_cycle_cap` | 6 | Partial — latch is OK; **UX is wrong** |
| `po_clarified` | 5 | Yes |
| `explore_budget_exhausted` | 4 | No — Cursor has no Explore/Patch budget |
| `tool_failure_stop` | 3 | Yes — real tool errors |
| `lane_advanced` / `completed_with_writes` | 2 | Yes — only wins in batch |

Representative traces:
- [`step-TASK-971A43DF…-20260923T001817.json`](file:///home/lukemcredmond/.cursor/projects/mnt-Storage-my-agents-my-agent-lab-DevelopmentAgent/attachments/42a02783-5eeb-4ba8-8b8e-b7c0b1888ec6/step-TASK-971A43DF16784FE4BD48D7C440BA4C55-20260923T001817.json) — 2 iterations, **0 tools**, identical apology text, `promptTokens≈19k`
- [`step-TASK-3FD5E15C…-20260922T231035.json`](file:///home/lukemcredmond/.cursor/projects/mnt-Storage-my-agents-my-agent-lab-DevelopmentAgent/attachments/42a02783-5eeb-4ba8-8b8e-b7c0b1888ec6/step-TASK-3FD5E15CBA1345949CF62DF6657D5AA0-20260922T231035.json) — `forcedToolMode: true`, `numPredict: 256`, **still text-only** → proves soft nudge is not Cursor-grade
- [`step-TASK-BD3E1823…-20260923T003342.json`](file:///home/lukemcredmond/.cursor/projects/mnt-Storage-my-agents-my-agent-lab-DevelopmentAgent/attachments/42a02783-5eeb-4ba8-8b8e-b7c0b1888ec6/step-TASK-BD3E1823338649D6BE7D952594755417-20260923T003342.json) — `phase_cycle_cap` with stale `suggestedAction: "Run In Progress again"` while latched

---

## Cursor principles vs current gaps

### 1. Tool-first agent loop (biggest gap)

**Cursor:** every agent turn is expected to emit tool calls (read → edit → verify); prose-only is not a valid step outcome.

**Current:** [`scrum_agent.py`](backend/agents/scrum_agent.py) rejects text and retries, but:
- `identical_text_reject` hard-stops at 2× **before** forced tool mode can recover ([`4245:4276:backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py))
- `forcedToolMode` only filters tool schemas; Ollama has **no `tool_choice: required`** path in [`llm_provider.py`](backend/services/llm_provider.py)
- Model echoes injected failure context: *"system has reached a state where it cannot proceed"* — classic prompt-poisoning

**Improvements:**
1. **Reorder rejection handling:** on first `text_rejected` during Dev/In Progress, enable `_forced_tool_mode` immediately (not after 3 rejects); on identical apology, **switch backup model** instead of `text_rejection_loop`.
2. **Tool-required fallback:** when `_forced_tool_mode` and response is still text-only, synthesize a minimal `read_file` on the task’s target path (from `lintSourceFile`, `writePaths`, or card title) — Cursor effectively never returns empty-handed.
3. **Park to Needs User** with split/reset options when 2 consecutive text-only turns occur on a lint/scaffold card — Cursor asks the human; it doesn’t spin in In Progress.

### 2. Prompt diet (Cursor keeps context tight)

**Current:** simple cards (`Fix SDK constraint in pubspec.yaml`) load **~19k prompt tokens** before the first LLM call. Prefill alone takes 35–37s (`promptEvalMs` in traces).

**Likely sources:** accumulated `=== PATCH RECOVERY ===`, `=== REFLECT ===`, fix-verify OBSERVE blocks, lint fanout, phase graph history, and card transcript replay.

**Improvements:**
4. **Forced-patch prompt slimming** in [`sprint_service._force_patch_dev_instruction`](backend/services/sprint_service.py) + [`llm_context.py`](backend/services/llm_context.py): on `forcePatchNextDevStep` or lint-wall cards, strip nonessential system injects (keep: target file, one lint diagnostic, one-line AC).
5. **Cap recovery injects per step** (max 1 PATCH RECOVERY + 1 REFLECT) in [`scrum_agent.py`](backend/agents/scrum_agent.py) — Cursor doesn’t stack 5 failure essays in context.
6. **Prompt budget guard:** if `promptTokens > 0.7 * num_ctx`, drop oldest non-system messages before next LLM call (mirror Cursor’s rolling window).

### 3. Native tool calls vs markdown recovery

**Current:** 62 `tool_calls_recovered_from_content` events in 51 steps (~1.2/step). [`eval_harness.py`](backend/services/eval_harness.py) already flags high recovery rate as unhealthy.

**Improvements:**
7. **Step-start tool probe** ([`tool_llm_probe.py`](backend/services/tool_llm_probe.py)): if native tool call fails on first turn, auto-route to backup coder model for the rest of the step (Cursor switches models silently).
8. **Raise parallel read batching** for recovered read tools — when recovery parses multiple `read_file` calls, execute via [`parallel_tools.partition_tool_calls`](backend/services/parallel_tools.py) instead of one-at-a-time.

### 4. Simpler edit loop (Cursor doesn’t use Explore/Patch/Verify budgets)

**Current:** [`dev_phase_graph.py`](backend/services/dev_phase_graph.py) still stops cards on explore budget even when forced patch is latched; this adds STOP messages the model mirrors back as apologies.

**Improvements:**
9. **Bypass phase budgets on forced-patch / lint-wall steps:** start directly in Patch with `maxToolsPerLlmTurn=2` (read + write), matching Cursor’s “read then edit” cadence.
10. **After successful `write_file`, skip re-explore** — go straight to verify/lint (already partially done; enforce in phase graph).

### 5. Honest terminal states (Cursor tells you when it’s blocked)

**Current:** `phase_cycle_cap` System steps show correct `agentResultSnippet` but [`_outcome_suggested_action`](backend/services/sprint_service.py) still returns *"Run In Progress again"* when `stopReason=phase_cycle_cap`.

**Improvements:**
11. Add `phase_cycle_cap` and `text_rejection_loop` branches to `_outcome_suggested_action` and [`step_diagnostics._build_hint`](backend/services/step_diagnostics.py):
    - phase cap → *"Split card or reset Developer visit latch"*
    - text loop → *"Model refused tools; try backup model or manual edit on {file}"*
12. Auto-park latched cards to **Needs User** with `needsUserKind=phase_cycle_cap` (code exists in [`needs_user_guard.py`](backend/services/needs_user_guard.py) but traces show many still In Progress/Done with latch).

### 6. Diagnostics to track Cursor-likeness (observability)

Add fields to step JSON ([`step_diagnostics.py`](backend/services/step_diagnostics.py)):

| Field | Purpose |
|---|---|
| `promptTokensAtFirstCall` | Detect bloat |
| `nativeToolCallRate` | `native toolCalls / total LLM calls` |
| `textOnlyTurns` | Count before stop |
| `forcedToolModeEffective` | true if any tool after forced mode |
| `cursorLikenessScore` | simple heuristic: wrote && native && duration < 180s |

Surface in TaskDetailModal alongside existing `cardToolFailures`.

---

## Recommended implementation order

### P0 — Stop text apology death spirals (Cursor agent core)

- Reorder text reject → forced tool mode → backup switch → Needs User (not hard stop)
- Tool-required fallback read on target file when forced mode still returns prose
- Fix `suggestedAction` / hints for `text_rejection_loop` and `phase_cycle_cap`

**Files:** [`backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py), [`backend/services/sprint_service.py`](backend/services/sprint_service.py), [`backend/services/needs_user_guard.py`](backend/services/needs_user_guard.py)

### P1 — Prompt diet + phase simplification

- Slim forced-patch / lint-wall prompts; cap recovery injects
- Bypass explore budget on forced-patch steps
- Prompt budget guard at 70% num_ctx

**Files:** [`backend/services/sprint_service.py`](backend/services/sprint_service.py), [`backend/services/llm_context.py`](backend/services/llm_context.py), [`backend/services/dev_phase_graph.py`](backend/services/dev_phase_graph.py)

### P2 — Tool reliability + diagnostics

- Step-start native tool probe → backup model routing
- Parallel batch recovered reads
- New Cursor-likeness diagnostic fields + UI

**Files:** [`backend/services/tool_llm_probe.py`](backend/services/tool_llm_probe.py), [`backend/services/step_diagnostics.py`](backend/services/step_diagnostics.py), [`frontend/src/components/TaskDetailModal.tsx`](frontend/src/components/TaskDetailModal.tsx)

---

## Validation (next sprint run)

Re-run representative stuck cards from this batch:
- `TASK-971A43DF` (pubspec SDK — text loop)
- `TASK-3FD5E15C` (analysis_options lint — forced mode ineffective)
- `TASK-BD3E1823` (drift repo — phase cap latch)

Success criteria (Cursor-like):
- No step exits `text_rejection_loop` without parking to Needs User or switching model
- First LLM call `promptTokens < 8000` on single-file lint cards
- `nativeToolCallRate > 0.5` OR backup model engaged
- Latched cards never show *"Run In Progress again"*
- At least one lint-wall card reaches QA with `fixVerifyLintClean: true`

Run tests: `tests/test_scrum_agent_read_continue.py`, `tests/test_text_rejection_circuit_breaker.py`, `tests/test_step_diagnostics.py`, plus new tests for suggestedAction/hint branches and forced-mode reorder.
