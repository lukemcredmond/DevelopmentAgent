"""Run the offline eval suite against the real sprint pipeline.

    python scripts/run_eval.py                     # all tasks, current settings
    python scripts/run_eval.py --only 01-add-function 02-fix-failing-test
    python scripts/run_eval.py --label baseline    # tag the run for later diffing
    python scripts/run_eval.py --dev-model qwen3:8b

Artifacts land in ~/.allhands/eval/ as JSON + markdown so runs can be compared.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import state  # noqa: E402
from backend.bootstrap import initialize  # noqa: E402
from backend.config import ensure_allhands_home  # noqa: E402
from backend.services.eval_harness import (  # noqa: E402
    DEFAULT_TASKS_DIR,
    format_report,
    load_tasks,
    run_suite,
    write_run_artifacts,
)
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings  # noqa: E402


def _apply_overrides(args: argparse.Namespace) -> None:
    updates = {}
    if args.iterations is not None:
        updates["maxLlmIterationsPerStep"] = int(args.iterations)
    if args.num_ctx is not None:
        updates["ollamaNumCtx"] = int(args.num_ctx)
    if args.single_model:
        updates["singleModelMode"] = "on"
    if updates:
        save_workflow_settings(updates)
        print(f"Applied setting overrides: {updates}")

    if args.dev_model:
        from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa

        for agent, key in ((agent_po, "po"), (agent_dev, "dev"), (agent_cr, "cr"), (agent_qa, "qa")):
            if key == "dev" or args.all_roles_same_model:
                agent.set_primary_model(args.dev_model)
                state.PRIMARY_MODELS[key] = args.dev_model
        print(f"Model override: dev={args.dev_model} all_roles={args.all_roles_same_model}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline agent eval suite.")
    parser.add_argument("--only", nargs="*", help="Task ids to run (default: all).")
    parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    parser.add_argument("--label", default="", help="Tag for the run artifacts.")
    parser.add_argument("--iterations", type=int, help="Override maxLlmIterationsPerStep.")
    parser.add_argument("--num-ctx", type=int, help="Override ollamaNumCtx.")
    parser.add_argument("--dev-model", help="Override the Developer model.")
    parser.add_argument(
        "--all-roles-same-model",
        action="store_true",
        help="Apply --dev-model to every role (avoids model swap thrash).",
    )
    parser.add_argument("--single-model", action="store_true", help="Force singleModelMode=on.")
    parser.add_argument("--verify-timeout", type=int, default=120)
    args = parser.parse_args()

    initialize()
    _apply_overrides(args)

    tasks = load_tasks(Path(args.tasks_dir), only=args.only)
    if not tasks:
        print("No eval tasks found.")
        return 1

    ws = get_workflow_settings()
    print(
        f"Running {len(tasks)} task(s) | model={state.PRIMARY_MODELS.get('dev')} "
        f"| iters={ws.get('maxLlmIterationsPerStep')} | num_ctx={ws.get('ollamaNumCtx')}"
    )
    print("-" * 70)

    def _progress(result) -> None:
        mark = "PASS" if result.passed else "FAIL"
        detail = result.failure_reason or ""
        print(f"[{mark}] {result.task_id} ({result.steps_run} steps, {result.wall_clock_sec}s) {detail}")

    results, summary = run_suite(tasks, verify_timeout_sec=args.verify_timeout, on_result=_progress)

    print("-" * 70)
    print(format_report(results, summary))

    out_dir = ensure_allhands_home() / "eval"
    path = write_run_artifacts(results, summary, out_dir, label=args.label)
    print(f"\nArtifacts: {path}")

    return 0 if summary["passed"] == summary["tasks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
