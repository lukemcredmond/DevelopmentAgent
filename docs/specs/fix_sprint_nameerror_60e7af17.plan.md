---
name: Fix sprint NameError
overview: Fix the `POST /api/sprint/run` 500 caused by calling `log_backlog_preflight_warnings()` from `_run_auto_sprint_body` without importing it in that function's scope.
todos:
  - id: fix-import-scope
    content: Move `log_backlog_preflight_warnings` import into `_run_auto_sprint_body` and remove dead import from `run_auto_sprint`
    status: completed
  - id: add-regression-test
    content: Assert backlog preflight is called during `run_auto_sprint` in `tests/test_auto_sprint_progress_boot.py`
    status: completed
  - id: verify-sprint-run
    content: Run targeted pytest and confirm `/api/sprint/run` no longer 500s
    status: completed
isProject: false
---

# Fix auto-sprint `NameError` on backlog preflight

## Root cause

`run_auto_sprint` was split into a wrapper and `_run_auto_sprint_body`, but the backlog preflight import/call were left in different scopes:

```6454:6486:backend/services/sprint_service.py
    import time

    from backend.services.backlog_preflight import log_backlog_preflight_warnings
    from backend.services.sprint_session import set_sprint_mode
    ...
        return _run_auto_sprint_body(
            brief, ollama_url, max_steps, continue_report=continue_report
        )
...
    brief = resolve_brief_for_sprint(brief)
    log_backlog_preflight_warnings()
```

The import at line 6456 is local to `run_auto_sprint`, so `_run_auto_sprint_body` raises `NameError` on every auto-sprint start.

The function itself exists and is correct in [`backend/services/backlog_preflight.py`](backend/services/backlog_preflight.py).

```mermaid
flowchart TD
    api["POST /api/sprint/run"] --> runAuto["run_auto_sprint()"]
    runAuto -->|"imports log_backlog_preflight_warnings"| localImport["local scope only"]
    runAuto --> body["_run_auto_sprint_body()"]
    body --> call["log_backlog_preflight_warnings()"]
    call --> error["NameError: not defined"]
```

## Fix

Make the import and call live in the same function.

**Preferred change** in [`backend/services/sprint_service.py`](backend/services/sprint_service.py):

1. Remove the unused import from `run_auto_sprint`.
2. Add the import inside `_run_auto_sprint_body`, immediately before the existing call (matching the lazy-import style already used there for `begin_sprint_report`).

This preserves the current execution order: sprint report init, brief resolution, backlog preflight warnings, then starting progress publish.

```python
def _run_auto_sprint_body(...):
    ...
    brief = resolve_brief_for_sprint(brief)
    from backend.services.backlog_preflight import log_backlog_preflight_warnings

    log_backlog_preflight_warnings()
    ws = get_workflow_settings()
    ...
```

No API or frontend changes are needed.

## Regression test

Add a small test in [`tests/test_auto_sprint_progress_boot.py`](tests/test_auto_sprint_progress_boot.py) (or a new focused test file) that patches `run_sprint_step` and asserts `run_auto_sprint(...)` completes without raising. The existing boot test already exercises this path but does not assert preflight is invoked; extend it with:

- `patch("backend.services.backlog_preflight.log_backlog_preflight_warnings")` and assert it is called once when auto sprint starts.

This would have caught the scoping bug immediately.

## Verification

After the fix:

1. Run the new/updated test:
   - `pytest tests/test_auto_sprint_progress_boot.py -q`
2. Manually retry `POST /api/sprint/run` from the UI or API client and confirm it returns 200 instead of 500.
