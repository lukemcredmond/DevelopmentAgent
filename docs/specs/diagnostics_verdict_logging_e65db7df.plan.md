---
name: Diagnostics verdict logging
overview: The latest 50 traces already show which sprint-loop improvements landed and which did not. Sampling options are still invisible in the JSON, so the next step is targeted diagnostic fields (plus a small exit-reason fix so successful writes stop looking like failures).
todos:
  - id: po-ws-order
    content: Keep execute_step get_workflow_settings() before PO sampling_options_for_role
    status: completed
  - id: sampling-snapshot
    content: Record sampling/model/num_ctx/num_predict on each step JSON
    status: completed
  - id: ollama-call-fields
    content: Add done_reason, durations, truncated, numPredict per ollama call
    status: completed
  - id: card-phase-rollup
    content: Log forcePatch, phase graph, write/failure rollups, PO laneAfterTool
    status: completed
  - id: exit-reason-writes
    content: Do not classify successful writes as max_iterations-only
    status: completed
  - id: tests
    content: Cover new diagnostic fields and write-vs-max-iter exit reason
    status: completed
isProject: false
---

# Diagnostic verdict and logging gaps

There is **enough information to judge the loop**, but **not enough to prove sampling settings**. The dump is 50 traces on `gemma-4-q4km:26b` (mealplanner), mixing morning and midday. Treat it as one job on the same code, as you said.

## Verdict: what worked

**Product Owner clarification mostly works now.** 6/7 PO steps exited `po_clarified` with a successful `update_board` to In Progress. Typical healthy PO is **1 LLM call**, ~900–1700 eval tokens, ~2 minutes of GPU. That is under the 2048 `num_predict` cap.

The truncated-retry path **did fire once** ([step-TASK-7FAB5AB30BC34BC9A84E155D9BFF6333-20260906T113447.json](file:///home/lukemcredmond/.cursor/projects/mnt-Storage-my-agents-my-agent-lab-DevelopmentAgent/attachments/e9f7f73c-7927-4d28-8a97-d92201de15d1/step-TASK-7FAB5AB30BC34BC9A84E155D9BFF6333-20260906T113447.json)): iter 4 used **exactly 2048** eval tokens with no tools, event `po_num_predict_bump num_predict=4096`, then iter 5 called `update_board`. So the PO cap + bump from [backend/services/po_clarification.py](backend/services/po_clarification.py) is live.

**Explore budget is doing its job.** 17 Dev steps stop at `explore_budget_exhausted` after 4 LLM turns of `list_dir`/`read_file`/`glob` instead of burning all 6. The next visit usually starts `Patch 0/4` (`forcePatchNextDevStep` in [backend/agents/scrum_agent.py](backend/agents/scrum_agent.py)). That is cheaper than the old read-only-until-max-iter pattern.

**Writes do happen on the forced-patch visit.** Many cards get a successful `apply_patch` or `write_file` on visit 2/4. One step even reached `completed_with_writes` (Store Management UI 2/2 at 11:18).

Uncommitted [backend/agents/scrum_agent.py](backend/agents/scrum_agent.py) + [tests/test_po_execute_step_sampling.py](tests/test_po_execute_step_sampling.py): HEAD still calls `sampling_options_for_role(..., ws=ws)` **before** `ws = get_workflow_settings()`. Keep that load-order fix; without it PO sampling from workflow settings is a `NameError` on a clean process.

## Verdict: what did not work

Cards still **do not finish**. 47/50 traces have `ok=False`. Dev almost never leaves In Progress. The repeating pattern on every card is:

```mermaid
flowchart LR
  v1[Visit1 Explore] -->|explore_budget_exhausted| v2[Visit2 Patch]
  v2 -->|writes then max_iterations| v3[Visit3 Explore again]
  v3 -->|explore_budget_exhausted| v4[Visit4 Patch]
  v4 -->|max_iterations again| stuck[Still In Progress]
```

**Why visit 3 goes back to Explore:** `forcePatchNextDevStep` is consumed on the next Dev step only. After a write+`max_iterations` visit, the flag is gone, so the phase graph resets to Explore and another 4-call explore tax is paid.

**Why writes look like failures:** [derive_exit_reason](backend/services/step_diagnostics.py) returns `max_iterations` whenever the agent result starts with `"Max tool iterations"`, **before** it checks `apply_patch`/`write_file`. So 24 steps are labeled `max_iterations` even when files were written. Only 1 of ~20 write-success steps is `completed_with_writes` (that one ended via fix-verify `"findings remain after 2 rounds"`, not the max-iter string). UI copy then claims the agent hit the limit *without writing edits*, which is false.

**Fix-verify never runs for real.** Almost every Dev step ends `fix_verify_done: aborted_hard_stop round=1`. The step burns 6 LLM turns, then verify is aborted.

**PO JSON still has two accuracy bugs:**
- One incomplete PO (04:41) has **0 eval tokens, 0 textChars, tokensReported false** — we cannot tell empty decode vs provider not reporting.
- Three `po_clarified` traces show `laneAfter: Done` and `ok=False` / `lastStepOutcome.exitReason: tool_failure_stop` even though the tool log is `update_board → In Progress`. Finalize is reading a later/global outcome, not the PO step’s own lane.

**Dev quality issues visible in toolsLog (not a sampling mystery):**
- `flutter pub get` / `flutter test` / `flutter analyze` fail repeatedly on “Update dependencies”.
- `apply_patch` context mismatches on the same dart files.
- “Explore Data Models” wrote `docs/data_models.md` instead of code.
- One step wrote `mealplanner/done_marker.txt` and tried `update_board → Done` (blocked).

PO wall-clock is still ~2 minutes **because eval is ~1000 tokens on a 26B Q4 model**, not because `num_predict` is wrong. Raising `num_predict` further will not make healthy one-shot PO faster.

## Logging to add (so the next dump answers the remaining questions)

Keep traces small; add fields, do not dump prompts.

1. **Step-level `sampling` snapshot** when the first chat options are built ([`ScrumAgent._chat_options`](backend/agents/scrum_agent.py)): `model`, `provider`, `temperature`, `top_p`, `repeat_penalty`, `num_predict`, `num_ctx`, `keep_alive`. This is the gap that currently makes “did PO/Dev sampling apply?” unanswerable.

2. **Per `ollamaCalls[]` row:** `numPredict`, `numCtx`, `truncated` (evalTokens >= num_predict-2), and Ollama `done_reason` / `prompt_eval_duration` / `eval_duration` if present on the raw response ([`extract_ollama_token_counts`](backend/services/agent_usage.py) + [`log_ollama_call`](backend/services/step_diagnostics.py)). That explains the 0-token PO incomplete and whether later PO bumps were needed.

3. **`cardCumulativeState` extras:** `forcePatchNextDevStep`, end-of-step phase graph `{phase, exploreCount, patchCount, verifyCount, writeSucceeded, cycle}`. Confirms the Explore→Patch→Explore oscillation without reading events by hand.

4. **Write / failure rollup on complete:** `writesAttempted`, `writesSucceeded`, `writePaths`, plus a short `toolFailureClasses` list (`patch_mismatch`, `command_nonzero`, `path_missing`, `board_blocked`) parsed from existing tool summaries. Truncate summaries less aggressively for failed `apply_patch`/`run_command` (first error line).

5. **PO-only:** `poJsonApplied`, `poNumPredictBumped`, `laneAfterTool` (from the `update_board` tool result, not finalize-time board). Stops Done/`tool_failure_stop` from contaminating PO traces.

6. **Exit-reason companion (small, same files):** in `derive_exit_reason`, if write tools succeeded, prefer `completed_with_writes` (or a new `max_iterations_after_writes`) over `max_iterations`. Without this, the next dump will keep looking like “Dev never writes.”

Tests: extend [tests/test_step_diagnostics.py](tests/test_step_diagnostics.py) and [tests/test_po_execute_step_sampling.py](tests/test_po_execute_step_sampling.py) for the new payload fields and the write-vs-max-iter classification.

No prompt or autonomy-budget changes in this pass unless you want a follow-up to persist `forcePatch` across a write+max_iter visit (that is the real Dev speed win; logging first so we can confirm it).
