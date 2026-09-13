---
name: Fix execute_step UnboundLocal
overview: Sprint `/api/sprint/run` 500s because `execute_step` treats `find_task_by_id` as a local due to a late inner import added for the cutoff summarizer. Remove that import (use the module-level one) and add a regression test. Optionally add a short SQLite busy timeout so concurrent health/settings reads do not raise `database is locked`.
todos:
  - id: fix-unboundlocal
    content: Remove inner find_task_by_id import in execute_step; use module-level import
    status: completed
  - id: sqlite-timeout
    content: Add sqlite timeout (and WAL if missing) so get_setting does not raise database is locked
    status: completed
  - id: regression-test
    content: Test execute_step with ACTIVE_SPRINT_TASK_ID does not UnboundLocalError
    status: completed
isProject: false
---

# Fix sprint UnboundLocalError (and SQLite lock)

The GVS5H ledger work is already in tree. Auto-sprint fails before it can run because of a Python scoping bug in the cutoff-summarizer hook.

## Root cause

[`backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py) imports `find_task_by_id` at module level (line 19). `execute_step` uses it at line 2832:

```python
if task_id:
    active_task = find_task_by_id(task_id)
```

The cutoff summarizer later does:

```3259:3259:backend/agents/scrum_agent.py
                            from backend.agents.task_context import find_task_by_id
```

In Python that inner `import` makes `find_task_by_id` a **local for the entire function**, so line 2832 raises `UnboundLocalError` on every Dev/PO `execute_step` — matching the traceback (`fix_verify_loop` → `execute_step` line 2832). The inner `try/except` never runs; the crash is at function entry.

The `sqlite3.OperationalError: database is locked` on `/api/ollama/health` is a separate race: [`get_workflow_settings`](backend/services/workflow_settings.py) → [`ProjectStorage.get_setting`](backend/storage/project_storage.py) uses `sqlite3.connect` with **no timeout** while a sprint thread holds the same `~/.allhands/scrum_memory.db`. Health polling then 500s. After the UnboundLocal fix, sprint still writes the DB heavily; health should not fail the UI.

## Fix

1. **Remove** the inner `from backend.agents.task_context import find_task_by_id` in `execute_step`. Keep only `from backend.services.card_ledger import is_length_cutoff, summarize_truncated_generation` and use the module-level `find_task_by_id`.
2. Grep `execute_step` for any other inner imports that shadow module-level names used earlier in the same function.
3. **SQLite:** add a helper `connect()` with `timeout=30` (and `PRAGMA journal_mode=WAL` once in `_init_db` if not already). Use it in `get_setting` / `set_setting` at minimum so concurrent health reads wait instead of raising. Retry `OperationalError` once on `database is locked` if still needed.

## Test

In [`tests/test_card_ledger.py`](tests/test_card_ledger.py) (or a small `test_scrum_agent_execute_step.py`):

- Patch `_chat` / tools so `execute_step` returns quickly.
- Call `execute_step` with `ACTIVE_SPRINT_TASK_ID` set and a board task present.
- Assert it does **not** raise `UnboundLocalError` (reaches `find_task_by_id` at the top of the function).

Optional: `get_setting` under a held write lock does not raise immediately when timeout is set (or mock is overkill — a unit test that `sqlite3.connect(..., timeout=30)` is used is enough).

Do not edit the GVS5H plan file.
