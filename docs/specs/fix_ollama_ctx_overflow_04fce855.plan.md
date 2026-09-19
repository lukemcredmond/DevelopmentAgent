---
name: Fix Ollama ctx overflow
overview: Packed prompt sizing sends `num_ctx=2048` because it only counts message text, so Ollama rejects tool-bearing prompts (~2461 tokens). The stream then sits empty until the 90s abort, which skips the overflow bump. Count tools in the pack, raise the floor, and surface Ollama 400s immediately so the call can retry at a larger window.
todos:
  - id: pack-tools
    content: Count tools in packed_prompt_num_ctx; floor 4096; pass tools from ScrumAgent
    status: completed
  - id: stream-400
    content: Raise immediately on Ollama stream error payloads so overflow bump runs
    status: completed
  - id: tests
    content: Update adaptive num_ctx tests and add stream 400 classification test
    status: completed
isProject: false
---

# Fix packed num_ctx undershoot vs Ollama tools

## What is happening

Workflow `ollamaNumCtx` is **32768**. On the first chat of a step, [`_ensure_packed_num_ctx`](backend/agents/scrum_agent.py) still sets `num_ctx` from [`packed_prompt_num_ctx`](backend/services/prompt_budget.py), which floors at **2048**.

That estimate is `chars // 4 + 1024` over **message content only** ([`estimate_messages_chars`](backend/services/llm_context.py)). Tool schemas are not counted, and `_ensure_packed_num_ctx` is not even passed `tools`.

Ollama tokenizes messages **+ tools** as **2461** against `n_ctx=2048` → HTTP 400 `exceed_context_size_error`.

Because chat is streamed, that 400 often never becomes a Python exception. [`consume_chat_stream`](backend/services/llm_provider.py) waits for eval tokens, then raises `EmptyGenerationTimeout` after **90s**. That type is **not retried** and **does not bump** `num_ctx` (only `context_overflow` does).

```mermaid
flowchart TD
  pack["_ensure_packed_num_ctx: messages chars/4 floor 2048"]
  call["Ollama /api/chat stream num_ctx=2048 tools included"]
  err400["400 exceed_context_size 2461 vs 2048"]
  hang["stream yields nothing"]
  timeout["empty gen timeout 90s no retry"]
  pack --> call --> err400 --> hang --> timeout
```

## Changes

### 1. Size `num_ctx` from messages + tools

In [`backend/services/prompt_budget.py`](backend/services/prompt_budget.py):

- Add an optional `tools` argument to `packed_prompt_num_ctx`.
- Include `json.dumps(tools)` (or equivalent) in the char estimate, same `// 4` heuristic.
- Raise the packed **floor from 2048 to 4096** (Workflow UI already uses min 4096 for `ollamaNumCtx`). Headroom stays 1024.

In [`backend/agents/scrum_agent.py`](backend/agents/scrum_agent.py):

- Pass `tools` into `_ensure_packed_num_ctx` from `_single_chat_attempt`.

A tiny “hello” + tools should pack to **4096**, which covers this 2461-token prompt without waiting for overflow.

### 2. Surface Ollama stream errors instead of hanging 90s

In [`_iter_ollama_stream` / `chat_result_from_ollama`](backend/services/llm_provider.py): if a chunk or the iterator error is a dict/object with `error` (or status 400 / `exceed_context_size`), **raise immediately** with that message.

Then `_classify_ollama_error` already maps `exceed_context` / `context size` to `context_overflow`, and `_chat` already bumps `num_ctx` (2048→4096, etc.) and retries.

Do not treat empty-generation timeout as overflow; fix the stream so the real 400 is the exception.

### 3. Tests

Update [`tests/test_adaptive_num_ctx.py`](tests/test_adaptive_num_ctx.py):

- Small prompt floor is **4096**, not 2048.
- Tools-only overhead: short messages + a fake tool schema pack above 2048 (expect 4096).

Add a stream test in [`tests/test_llm_provider.py`](tests/test_llm_provider.py) or [`tests/test_ollama_retry.py`](tests/test_ollama_retry.py):

- Iterator whose first item / raise is `exceed_context_size_error` → classified as `context_overflow`, not empty-gen timeout.
- Existing overflow bump test still doubles and retries.

Run: `tests/test_adaptive_num_ctx.py`, `tests/test_ollama_retry.py`, `tests/test_llm_provider.py`.

## Out of scope

- Changing the 32768 workflow ceiling or empty-gen 90s setting.
- Counting `num_predict` into packed `num_ctx` (Ollama already reserves generation inside `n_ctx`; 4096 floor + 1024 headroom is enough for this failure).
