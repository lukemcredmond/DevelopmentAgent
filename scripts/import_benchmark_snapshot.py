#!/usr/bin/env python3
"""Import a benchmark JSON snapshot into ~/.allhands settings storage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.bootstrap import initialize
from backend.services.model_benchmark_runner import import_benchmark_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Import model benchmark snapshot JSON")
    parser.add_argument("--file", required=True, help="Path to benchmark JSON file")
    parser.add_argument(
        "--url",
        default="http://localhost:11434",
        help="LLM base URL for storage key (default: http://localhost:11434)",
    )
    args = parser.parse_args()
    path = Path(args.file)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("JSON root must be an object", file=sys.stderr)
        return 1
    initialize()
    result = import_benchmark_snapshot(data, url=args.url)
    if result.get("status") == "blocked":
        print(result.get("error") or "blocked", file=sys.stderr)
        return 1
    print(
        f"Imported {len(result.get('results') or [])} models; "
        f"best={result.get('bestModel')}; status={result.get('status')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
