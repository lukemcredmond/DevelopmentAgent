---
name: LM Studio sprint load
overview: Auto sprint never explicitly loads the model in LM Studio (the Test button does). Sprint only POSTs `/v1/chat/completions`, so a JIT/full-KV load can be refused with no model in the UI and an empty Logs tab, while the app Console still fills with local sprint messages.
todos:
  - id: ensure-loaded
    content: Add OpenAICompatProvider.ensure_model_loaded + call it from chat(); reuse native /api/v1 load; skip if already loaded; retry smaller ctx on memory refuse
    status: completed
  - id: pass-num-ctx
    content: Always set options.num_ctx in ScrumAgent._chat_options; use it for LM Studio load context
    status: completed
  - id: logs-tests
    content: Log load attempts with real base URL; add provider tests for load-then-chat, no double-load, Ollama unchanged
    status: completed
isProject: false
---

# Fix Auto Sprint not loading LM Studio

## Why Test works and Auto Sprint does not

Two different code paths talk to LM Studio:

- **Test** ([`backend/services/model_test_runner.py`](backend/services/model_test_runner.py)): `unload_loaded_except` → **`load_model_for_test`** (`POST {host}/api/v1/models/load` with `context_length=4096`) → tiny `/v1/chat/completions`. That native load is what makes the model appear in LM Studio and opens its log stream.
- **Auto sprint** ([`backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py) `_chat` → [`OpenAICompatProvider.chat`](backend/services/llm_provider.py)): only `POST {base}/v1/chat/completions`. No native load. Console lines such as “Sprint handler…”, “Implementing…”, “Waiting for model (Ollama)” are **local** logs, so the Console tab moves even when LM Studio never starts an instance.

Comments already document the failure mode:

```20:21:backend/services/model_test_runner.py
# Just-in-time loads otherwise reserve a full-size cache and
# can be refused by a memory guardrail on large models.
```

Sprint also asks for a large window (`ollamaNumCtx` default **32768**). For OpenAI-compat, `num_ctx` is **not** sent (provider `capabilities.num_ctx = False`), so LM Studio JIT uses its own full default. That is exactly the load the Test path was written to avoid. If JIT is off, chat also never loads a model.

Connection/settings are unlikely: Test and Auto Sprint share `get_chat_provider(override_url=…)` and [`chat_config`](backend/services/llm_provider.py) already ignores a stale `http://localhost:11434` override when the preset is LM Studio.

```mermaid
sequenceDiagram
  participant UI
  participant Sprint
  participant Provider
  participant LMStudio
  UI->>Sprint: POST /api/sprint/run
  Sprint->>Sprint: Console logs handler and wait
  Sprint->>Provider: chat completions only
  Provider->>LMStudio: POST /v1/chat/completions
  Note over LMStudio: JIT/full KV often refused; no instance, empty Logs
  UI->>Provider: Test model
  Provider->>LMStudio: POST /api/v1/models/load ctx=4096
  LMStudio-->>UI: Model visible and logs
  Provider->>LMStudio: POST /v1/chat/completions
```

## Fix

Load the model the same way Test does, on every OpenAI-compat **chat** (sprint, Plan & Run, Execute Step, chat panel), not only the Test button.

1. **Provider: `ensure_model_loaded`** in [`backend/services/llm_provider.py`](backend/services/llm_provider.py)
   - Extract shared native load from `load_model_for_test`.
   - Before `chat/completions`, if the model is not already loaded (check `/api/v1/models` `loaded_instances`):
     - Optionally `unload_loaded_except` when swapping models (same as Test).
     - `POST /api/v1/models/load` with an explicit `context_length`.
   - Cache `(model, context_length)` on the provider instance so tool-loop turns do not reload.
   - If load is refused (memory / HTTP 4xx), retry with halved context down to 4096 (Test’s known-good size), and log each attempt via `add_system_log`.
   - If native `/api/v1` is missing (`unavailable`), fall through to today’s JIT chat and log that.

2. **Context size for sprint, not 4096-only**
   - Always put `_effective_num_ctx()` into chat `options["num_ctx"]` in [`ScrumAgent._chat_options`](backend/agents/scrum_agent.py), even when `capabilities.num_ctx` is false (OpenAI payload still omits it; load API uses it).
   - `OpenAICompatProvider.chat` reads `options.num_ctx` (else workflow `ollamaNumCtx` / VRAM fit) for the load call.

3. **Visible diagnostics**
   - Console: `Loading {model} in LM Studio (context={n})` / `LM Studio already has {model} loaded`.
   - On chat failure, keep existing “All Ollama attempts failed…” but include the **actual** `provider.base_url` so a wrong host is obvious.

4. **Tests** in [`tests/test_llm_provider.py`](tests/test_llm_provider.py)
   - First OpenAI `chat` hits `/api/v1/models/load` then `/v1/chat/completions`.
   - Second `chat` with the same model does not reload.
   - Load uses `options.num_ctx`.
   - Ollama `chat` still does not call `/api/v1/models/load`.

Ollama behavior stays unchanged (`load_model_for_test` remains unsupported there).

## How you can confirm after the fix

- Enable Auto Sprint: LM Studio should show the role model loading **before** the first completion, same as Test.
- Console should show a load line, then “Waiting for model…”.
- LM Studio developer/server logs should show `/api/v1/models/load` then `/v1/chat/completions`.
