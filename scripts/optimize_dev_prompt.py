#!/usr/bin/env python3
"""Offline optimize the Developer system prompt from recent step diagnostics.

Usage:
  python scripts/optimize_dev_prompt.py --limit 50
  python scripts/optimize_dev_prompt.py --limit 50 --apply

Optional deps (recommended): pip install -r requirements-optimize.txt
Without them, a heuristic + optional Ollama reflection still runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize Dev system prompt from step traces")
    parser.add_argument("--limit", type=int, default=50, help="Max recent step diagnostics")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write result into workflow agentPrompts.Developer.system",
    )
    parser.add_argument(
        "--reflection-model",
        default="",
        help="Ollama model for reflective rewrite (default: discord quality preset / coder)",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    from backend.services.prompt_optimize import run_optimize

    result = run_optimize(
        limit=max(1, args.limit),
        apply=bool(args.apply),
        reflection_model=str(args.reflection_model or ""),
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"traces={result['traceCount']} mean_score={result['meanScore']} method={result['method']}")
        print(f"wrote: {result['outputPath']}")
        if result.get("applied"):
            print("applied to workflow agentPrompts.Developer.system")
        else:
            print("Paste into Workflow → Agent prompts → Developer system, or re-run with --apply")
        print(result.get("note") or "")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
