# GVS5H learnings in All Hands

Source: [slee-persis/GVS5H](https://github.com/slee-persis/GVS5H) (MIT code patterns only; not the LiveCodeBench harness).

## Transplanted

| Flag (default on) | Behavior |
|---|---|
| `enableCardLedger` | Bounded files under `{workspace}/.allhands/cards/{task_id}/` (`plan.md`, `notes.md`, `tasks.json`, `last_oracle.txt`). Injected in the Dev prompt **before** the transcript. |
| `enableDevIdeation` | First Dev visit with empty notes and no writes: one no-code brainstorm into `notes.md`. |
| `enableOracleDoneOverride` | Failing lint/test oracle vetoes Done / QA advance. |
| `enableSameNextTaskStop` | Reissued identical next-work with no writes parks the card. |
| `enableCutoffSummarizer` | Non-empty `finish_reason=length` → short salvage note. Empty generations are **not** retried. |

## Out of scope (intentionally)

- Five concurrent copies of the same model
- Replacing PO/Dev/CR/QA with a research manager
- Contest grading / `run_bench.py`

Code: `backend/services/card_ledger.py`.
