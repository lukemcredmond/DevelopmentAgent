"""Optional Graphify CLI integration for structural codebase graphs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend import state


def _workspace_dir() -> Path:
    ws = (state.WORKSPACE_DIR or "").strip()
    if ws and os.path.isdir(ws):
        return Path(ws)
    return Path(".")


def graphify_available() -> bool:
    return shutil.which("graphify") is not None


def graph_report_path() -> Path:
    return _workspace_dir() / "GRAPH_REPORT.md"


def graph_json_path() -> Path:
    return _workspace_dir() / "graph.json"


def run_graphify_update(*, timeout_sec: int = 300) -> Dict[str, Any]:
    """Run `graphify <workspace> --update` when the CLI is installed."""
    if not graphify_available():
        return {"ok": False, "skipped": True, "error": "graphify CLI not found on PATH"}
    workspace = str(_workspace_dir())
    try:
        proc = subprocess.run(
            ["graphify", workspace, "--update"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=workspace,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "graphify update timed out"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "graphify failed").strip()[:500]
        return {"ok": False, "error": err}
    return {
        "ok": True,
        "reportPath": str(graph_report_path()),
        "graphPath": str(graph_json_path()),
        "stdout": (proc.stdout or "")[:300],
    }


def graphify_status() -> Dict[str, Any]:
    report = graph_report_path()
    graph = graph_json_path()
    return {
        "available": graphify_available(),
        "reportExists": report.is_file(),
        "graphExists": graph.is_file(),
        "reportPath": str(report),
        "graphPath": str(graph),
        "reportChars": report.stat().st_size if report.is_file() else 0,
    }


def run_graph_query(query: str, *, timeout_sec: int = 60) -> str:
    """Run `graphify query` or fall back to capped GRAPH_REPORT.md excerpt."""
    query = (query or "").strip()
    if not query:
        return "Error: query is required."

    if graphify_available():
        workspace = str(_workspace_dir())
        try:
            proc = subprocess.run(
                ["graphify", "query", query],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=workspace,
            )
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return proc.stdout.strip()[:8000]
            if proc.stderr:
                return f"Graphify query failed: {proc.stderr.strip()[:500]}"
        except subprocess.TimeoutExpired:
            return "Graphify query timed out."
        except OSError as exc:
            return f"Graphify query error: {exc}"

    report = graph_report_path()
    if report.is_file():
        text = report.read_text(encoding="utf-8", errors="replace")
        lowered = query.lower()
        lines = text.splitlines()
        hits: List[str] = []
        for i, line in enumerate(lines):
            if lowered in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                hits.extend(lines[start:end])
        if hits:
            excerpt = "\n".join(dict.fromkeys(hits))
            return f"=== GRAPH REPORT EXCERPT (graphify CLI unavailable) ===\n{excerpt[:6000]}"
        return text[:6000]

    graph = graph_json_path()
    if graph.is_file():
        try:
            data = json.loads(graph.read_text(encoding="utf-8", errors="replace"))
            nodes = data.get("nodes") or data.get("entities") or []
            if isinstance(nodes, list):
                matched = []
                for node in nodes[:200]:
                    if not isinstance(node, dict):
                        continue
                    label = str(node.get("label") or node.get("name") or node.get("id") or "")
                    if query.lower() in label.lower():
                        matched.append(label)
                if matched:
                    return "Graph nodes matching query:\n" + "\n".join(f"- {m}" for m in matched[:40])
        except (json.JSONDecodeError, OSError):
            pass

    return (
        "No Graphify graph found. Install graphify CLI and run Reindex, "
        "or ensure GRAPH_REPORT.md exists in the workspace."
    )


def build_graphify_sprint_context(active_task: Dict[str, Any], *, max_chars: int = 2500) -> str:
    """Inject a capped structural graph excerpt into sprint prompts."""
    title = str(active_task.get("title") or "")
    desc = str(active_task.get("description") or "")
    query = f"{title} {desc}".strip()[:200]
    if not query:
        return ""
    result = run_graph_query(query)
    if result.startswith("Error:") or result.startswith("No Graphify"):
        report = graph_report_path()
        if report.is_file():
            text = report.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[:max_chars] + "\n…[truncated]"
            return f"\n=== CODE STRUCTURE GRAPH (Graphify) ===\n{text}\n"
        return ""
    if len(result) > max_chars:
        result = result[:max_chars] + "\n…[truncated]"
    return f"\n=== CODE STRUCTURE GRAPH (Graphify) ===\n{result}\n"
