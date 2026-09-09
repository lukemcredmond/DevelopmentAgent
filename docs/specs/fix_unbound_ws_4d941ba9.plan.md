---
name: Fix unbound ws
overview: Fix the UnboundLocalError in Product Owner `execute_step` by loading workflow settings before they are passed to sampling, then add a small regression test so PO sprints no longer 500.
todos:
  - id: reorder-ws
    content: Move get_workflow_settings() above the Product Owner sampling block in execute_step
    status: completed
  - id: regression-test
    content: Add a test that PO execute_step sets _step_num_predict without UnboundLocalError
    status: completed
isProject: false
---

# Fix UnboundLocalError on PO sprint start

## Cause

In [`backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py) `execute_step`, the Product Owner branch uses `ws` **before** it is assigned:

```2469:2476:backend/agents/scrum_agent.py
        if self.role == "Product Owner":
            from backend.services.po_clarification import PO_NUM_PREDICT_DEFAULT
            from backend.services.sampling import sampling_options_for_role

            po_opts = sampling_options_for_role(self.role, ws=ws)
            self._step_num_predict = int(po_opts.get("num_predict") or PO_NUM_PREDICT_DEFAULT)
        ws = get_workflow_settings()
```

Python treats `ws` as a local for the whole function because of the later assignment, so PO clarification (`POST /api/sprint/run` → `_run_po_clarification` → `agent_po.execute_step`) raises `UnboundLocalError` and returns 500.

[`sampling_options_for_role`](backend/services/sampling.py) already loads settings when `ws` is omitted, but the intended pattern (see `_chat_options` around line 1062) is to fetch once and reuse.

## Fix

In `execute_step`, call `ws = get_workflow_settings()` **before** the Product Owner `if` block, then keep using that `ws` for `sampling_options_for_role` and for `maxToolFailuresPerStep` / duration / tool-call limits.

No other call sites need the same change: `_chat_options` already assigns `ws` first.

## Test

Add a focused test (new file or existing scrum/PO test module) that:

- Instantiates/uses the Product Owner agent
- Mocks LLM/tools so `execute_step` does not need a live Ollama
- Asserts that entering the PO branch does not raise, and `_step_num_predict` is set from sampling (default 2048 from `ROLE_SAMPLING_DEFAULTS["po"]` / `PO_NUM_PREDICT_DEFAULT`)

A lightweight approach: patch `get_workflow_settings`, `_chat` / provider, and return quickly, or extract nothing extra and just call `execute_step` with mocks that exit on the first LLM response.

## Verification

Run the new test plus any existing PO/sprint tests that hit `execute_step` for Product Owner.
