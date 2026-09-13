---
name: README timeout docs
overview: Update README defaults and retry wording so they match the current Ollama timeout behavior (900s, timeouts not retried). This is a small docs-only edit; the rest of the README is still accurate.
todos:
  - id: readme-timeout-rows
    content: Fix ollamaRequestTimeoutSec (900) and ollamaMaxRetries wording in the Workflow settings table
    status: completed
  - id: readme-llm-runtime
    content: Align LLM runtime retries/timeouts sentence with timeout-not-retried behavior
    status: completed
isProject: false
---

# README needs a small Ollama timeout update

**Yes.** The rest of [README.md](README.md) still matches the product. The gap is only the uncommitted Ollama timeout / no-retry work.

Code and UI already use **900s** and **do not retry HTTP timeouts** with a new chat:

- Default in [backend/services/workflow_settings.py](backend/services/workflow_settings.py): `ollamaRequestTimeoutSec: 900`
- UI hint in [frontend/src/components/WorkflowPanel.tsx](frontend/src/components/WorkflowPanel.tsx): “Per-attempt HTTP timeout. Timeouts are not retried with a new chat (default 900s).”
- Retry logic in [backend/agents/scrum_agent.py](backend/agents/scrum_agent.py): `err_type == "timeout"` is final; connection / 5xx / idle-server still retry.

README still says the old contract.

## What to change

In **Workflow settings (full reference)** (~lines 475–476):

- `ollamaRequestTimeoutSec` default **300 → 900**, and purpose: per-attempt HTTP timeout (not retried with a new chat).
- `ollamaMaxRetries` purpose: retries **connection / 5xx / idle-server** failures; **timeouts are not retried**.

In **AI technology → LLM runtime** (~line 1086):

- Tighten **Retries / timeouts** so it does not imply every HTTP timeout is retried.

Optional one-liner in **Troubleshooting**: long Console silence during a large `write_file` is expected until the 900s timeout; look for `Still waiting for Ollama` every 15s. Not required for correctness.

Do **not** rewrite recommended-settings tables, agent workflow, or API sections — they are unrelated.

## Out of scope

Do not document the Forced Patch / duplicate-test stop unless you want those called out separately; they are not currently misstated as defaults in the settings table.