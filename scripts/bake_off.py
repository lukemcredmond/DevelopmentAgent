"""Rank locally available models by how well they actually complete agent tasks.

    python scripts/bake_off.py                      # every installed model
    python scripts/bake_off.py --models a:7b b:7b   # a specific shortlist
    python scripts/bake_off.py --only 01-add-function 02-fix-failing-test

Benchmarks and blog posts measure single-turn code generation, which is not the job
here. What matters for this pipeline is whether a model can sustain a multi-turn tool
loop: emit *native* tool calls, patch precisely, and finish. The two headline numbers
are task pass rate and native tool-call rate (how often the model produced usable
tool calls without `llm_tool_recovery` having to scrape JSON out of prose).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import state  # noqa: E402
from backend.bootstrap import initialize  # noqa: E402
from backend.config import ensure_allhands_home  # noqa: E402
from backend.services.eval_harness import DEFAULT_TASKS_DIR, load_tasks, run_suite  # noqa: E402
from backend.services.llm_provider import get_chat_provider  # noqa: E402
from backend.services.workflow_settings import save_workflow_settings  # noqa: E402

# Embedding models cannot drive an agent loop; never waste a suite run on them.
NON_CHAT_HINTS = ("embed", "bge-", "nomic-embed", "all-minilm", "gte-")


def discover_models() -> List[str]:
    provider = get_chat_provider()
    health = provider.health()
    if not health.ok:
        raise SystemExit(f"LLM endpoint not reachable at {health.url}: {health.error}")
    return [m for m in health.models if not any(h in m.lower() for h in NON_CHAT_HINTS)]


def set_all_roles(model: str) -> None:
    """Point every role at one model so the run measures the model, not swap overhead."""
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa

    for agent, key in ((agent_po, "po"), (agent_dev, "dev"), (agent_cr, "cr"), (agent_qa, "qa")):
        agent.set_primary_model(model)
        state.PRIMARY_MODELS[key] = model


def score_row(model: str, summary: Dict[str, Any], elapsed: float) -> Dict[str, Any]:
    steps = max(1, int(summary.get("totalSteps") or 0))
    recovery_rate = float(summary.get("toolRecoveryRate") or 0.0)
    return {
        "model": model,
        "passRate": summary.get("passRate", 0.0),
        "passed": summary.get("passed", 0),
        "tasks": summary.get("tasks", 0),
        # The inverse of recovery rate: how often tool calls arrived in native form.
        "nativeToolCallRate": round(1.0 - recovery_rate, 3),
        "budgetExhaustedRate": summary.get("budgetExhaustedRate", 0.0),
        "avgTokensPerSec": summary.get("avgTokensPerSec"),
        "totalSteps": steps,
        "wallClockSec": round(elapsed, 1),
        "exitReasons": summary.get("exitReasons", {}),
    }


def format_table(rows: List[Dict[str, Any]]) -> str:
    header = (
        f"{'model':<34} {'pass':>7} {'native':>7} {'budget':>7} {'tok/s':>7} {'sec':>7}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        tps = row["avgTokensPerSec"]
        lines.append(
            f"{row['model'][:34]:<34} "
            f"{row['passed']}/{row['tasks']:<5} "
            f"{row['nativeToolCallRate'] * 100:6.0f}% "
            f"{row['budgetExhaustedRate'] * 100:6.0f}% "
            f"{(f'{tps:.1f}' if tps else '-'):>7} "
            f"{row['wallClockSec']:>7.0f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank local models on the agent eval suite.")
    parser.add_argument("--models", nargs="*", help="Models to test (default: all installed).")
    parser.add_argument("--only", nargs="*", help="Task ids to run (default: all).")
    parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--verify-timeout", type=int, default=120)
    args = parser.parse_args()

    initialize()
    # One model for every role keeps the comparison about model quality, and stops a
    # VRAM-limited host from thrashing between models mid-suite.
    save_workflow_settings(
        {"singleModelMode": "on", "maxLlmIterationsPerStep": int(args.iterations)}
    )

    models = args.models or discover_models()
    if not models:
        print("No chat-capable models found.")
        return 1

    tasks = load_tasks(Path(args.tasks_dir), only=args.only)
    print(f"Bake-off: {len(models)} model(s) x {len(tasks)} task(s)")
    print("=" * 78)

    rows: List[Dict[str, Any]] = []
    for model in models:
        print(f"\n### {model}")
        set_all_roles(model)
        started = time.time()
        try:
            _, summary = run_suite(tasks, verify_timeout_sec=args.verify_timeout)
        except Exception as exc:
            print(f"  run failed: {type(exc).__name__}: {exc}")
            continue
        row = score_row(model, summary, time.time() - started)
        rows.append(row)
        print(
            f"  pass {row['passed']}/{row['tasks']} | native tool calls "
            f"{row['nativeToolCallRate'] * 100:.0f}% | {row['wallClockSec']:.0f}s"
        )

    if not rows:
        print("\nNo models completed a run.")
        return 1

    # Completion first; native tool calling breaks ties because a model that needs
    # constant recovery is fragile even when it occasionally succeeds.
    rows.sort(key=lambda r: (r["passRate"], r["nativeToolCallRate"]), reverse=True)

    print("\n" + "=" * 78)
    print(format_table(rows))
    print(f"\nWinner: {rows[0]['model']}")

    out_dir = ensure_allhands_home() / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"bakeoff-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    print(f"Artifacts: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
