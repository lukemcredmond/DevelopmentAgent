"""Offline Dev system-prompt optimization helpers (GEPA/DSPy optional).

Core app does not depend on dspy/gepa — scoring and export work without them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import ensure_allhands_home
from backend.services.training_export import _load_recent_step_files, step_trace_to_export_row


def optimized_prompts_dir() -> Path:
    path = ensure_allhands_home() / "optimized_prompts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def score_step_trace(data: Dict[str, Any]) -> float:
    """
    Local metric for offline prompt optimization (0.0–1.0-ish, can go slightly outside).

    Reward writes / success; penalize thrash (high LLM:tool, failures, stuck exits).
    Fail-open: sparse traces score near neutral 0.5.
    """
    if not isinstance(data, dict) or not data:
        return 0.5

    score = 0.5
    stop = str(
        data.get("exitReason")
        or data.get("stopReason")
        or data.get("exit_reason")
        or ""
    ).lower()
    ok = data.get("ok")
    tools = data.get("toolsUsed") or data.get("tools") or []
    if isinstance(tools, set):
        tools = list(tools)
    if not isinstance(tools, list):
        tools = []
    tool_names = {
        str(t.get("toolName") or t.get("name") or t).lower()
        if isinstance(t, dict)
        else str(t).lower()
        for t in tools
    }

    write_tools = {"write_file", "apply_patch"}
    has_write = bool(tool_names & write_tools)

    if ok is True or stop in ("completed_with_writes", "completed", "task_done"):
        score += 0.35
    elif stop in ("completed_text_only",):
        score += 0.05
    if has_write:
        score += 0.2

    # Penalties
    if stop in (
        "max_iterations",
        "duplicate_tool",
        "tool_failure_stop",
        "step_timeout",
        "plan_exhausted",
        "read_only_no_edits",
        "explore_budget",
        "phase_stuck",
    ):
        score -= 0.25
    if "stuck" in stop or "explore" in stop and "budget" in stop:
        score -= 0.1

    llm_calls = int(data.get("llmCalls") or data.get("llm_calls") or 0)
    tool_calls = int(data.get("toolCalls") or data.get("tool_calls") or len(tools) or 0)
    failed = int(data.get("failedTools") or data.get("toolFailures") or data.get("tool_failures") or 0)
    ollama = data.get("ollamaCalls") or data.get("ollama_calls") or []
    if isinstance(ollama, list) and ollama and not llm_calls:
        llm_calls = len(ollama)
    tools_log = data.get("toolsLog") or data.get("tools_log") or []
    if isinstance(tools_log, list) and tools_log and not tool_calls:
        tool_calls = len(tools_log)
        failed = sum(1 for e in tools_log if isinstance(e, dict) and e.get("success") is False)

    if llm_calls > 0 and tool_calls >= 0:
        ratio = llm_calls / max(tool_calls, 1)
        if ratio > 6:
            score -= min(0.3, (ratio - 6) * 0.04)
        elif ratio <= 4 and tool_calls > 0:
            score += 0.08

    if failed >= 3:
        score -= min(0.25, failed * 0.04)
    elif failed == 0 and tool_calls > 0:
        score += 0.05

    # AC / card progress if present
    lsp = data.get("lastStepProgress") or data.get("cardProgress") or {}
    if isinstance(lsp, dict):
        cp = lsp.get("cardProgress") if isinstance(lsp.get("cardProgress"), dict) else lsp
        if isinstance(cp, dict):
            try:
                done = int(cp.get("acsDone") or cp.get("acDone") or 0)
                total = int(cp.get("acsTotal") or cp.get("acTotal") or 0)
                if total > 0 and done > 0:
                    score += 0.1 * (done / total)
            except (TypeError, ValueError):
                pass

    return max(0.0, min(1.2, score))


def load_scored_traces(*, limit: int = 50) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for data in _load_recent_step_files(limit=limit):
        export = step_trace_to_export_row(data)
        export["score"] = score_step_trace(data)
        export["raw"] = {
            "exitReason": data.get("exitReason") or data.get("stopReason"),
            "ok": data.get("ok"),
            "llmCalls": data.get("llmCalls"),
            "toolCalls": data.get("toolCalls"),
            "failedTools": data.get("failedTools"),
        }
        rows.append(export)
    return rows


def mean_trace_score(traces: List[Dict[str, Any]]) -> float:
    if not traces:
        return 0.5
    return sum(float(t.get("score") or 0.5) for t in traces) / len(traces)


def seed_dev_system_prompt() -> str:
    from backend.services.prompt_defaults import get_effective_system_prompt
    from backend.services.workflow_settings import get_workflow_settings

    return get_effective_system_prompt("Developer", get_workflow_settings()).strip()


def next_version_path(*, stem: str = "dev_system") -> Path:
    directory = optimized_prompts_dir()
    existing = sorted(directory.glob(f"{stem}_v*.txt"))
    max_n = 0
    for path in existing:
        m = re.search(r"_v(\d+)\.txt$", path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return directory / f"{stem}_v{max_n + 1}.txt"


def write_optimized_prompt(text: str, *, stem: str = "dev_system") -> Path:
    path = next_version_path(stem=stem)
    header = (
        f"# Optimized {stem} — {datetime.now(timezone.utc).isoformat()}\n"
        "# Paste into Workflow → Agent prompts → Developer system, or re-run with --apply\n\n"
    )
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    return path


def apply_dev_system_prompt(text: str) -> Dict[str, Any]:
    """Write optimized Dev system prompt into live workflow agentPrompts."""
    from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings

    cleaned = text.strip()
    # Strip our file header comments if present
    lines = cleaned.splitlines()
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError("Optimized prompt is empty")
    current = get_workflow_settings()
    ap = dict(current.get("agentPrompts") or {})
    role_cfg = dict(ap.get("Developer") or {})
    role_cfg["system"] = cleaned
    ap["Developer"] = role_cfg
    return save_workflow_settings({"agentPrompts": ap})


def heuristic_improve_prompt(seed: str, traces: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Fallback when GEPA/DSPy are not installed: append concise guidance from weak traces.
    """
    weak = [t for t in traces if float(t.get("score") or 0.5) < 0.45]
    tips: List[str] = []
    if any("max_iterations" in str(t.get("stopReason") or "").lower() for t in weak):
        tips.append("Prefer apply_patch early after at most a few read_file calls; avoid plan-only replies.")
    if any("duplicate" in str(t.get("stopReason") or "").lower() for t in weak):
        tips.append("Never repeat the same tool args; change path or approach after a fingerprint block.")
    if any(float(t.get("score") or 0) < 0.4 for t in weak):
        tips.append("Keep explore reads bounded; move to write/verify once you have enough context.")
    if not tips:
        tips.append("Use tools to edit files; do not stop on text-only plans.")
    block = "\n".join(f"- {t}" for t in tips[:4])
    improved = seed.rstrip() + "\n\n### Efficiency lessons (offline heuristic)\n" + block
    return improved, "heuristic"


def try_gepa_optimize(
    seed: str,
    traces: List[Dict[str, Any]],
    *,
    max_metric_calls: int = 20,
    reflection_model: str = "",
    task_model: str = "",
) -> Tuple[str, str]:
    """
    Attempt GEPA (standalone) or dspy.GEPA. Raises ImportError if unavailable.
    For local Ollama, prefers a light reflection pass over full GEPA when trace count is tiny.
    """
    if len(traces) < 3:
        return heuristic_improve_prompt(seed, traces)

    # Prefer standalone gepa with a simple reflective rewrite using Ollama if available.
    try:
        import gepa  # type: ignore  # noqa: F401
    except ImportError:
        gepa = None  # type: ignore

    try:
        import dspy  # type: ignore
    except ImportError:
        dspy = None  # type: ignore

    if gepa is None and dspy is None:
        raise ImportError("Neither gepa nor dspy is installed")

    # Lightweight reflective optimize via Ollama chat (works without cloud GEPA budget).
    # Full gepa.optimize against live ScrumAgent would be expensive; use reflection LM.
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    model = (
        reflection_model
        or task_model
        or str(ws.get("discordModelPresetQuality") or "").strip()
        or "qwen2.5-coder:14b"
    )
    summary_lines = []
    for t in traces[:30]:
        summary_lines.append(
            f"- score={t.get('score'):.2f} stop={t.get('stopReason')} "
            f"tools={','.join(str(x) for x in (t.get('tools') or [])[:6])}"
        )
    summary = "\n".join(summary_lines)
    prompt = (
        "You optimize a Developer agent system prompt for a local coding agent.\n"
        "Rewrite the system prompt to improve step success (writes, fewer failed/duplicate tools, "
        "lower LLM:tool thrash). Keep it under 1200 characters. Return ONLY the new system prompt.\n\n"
        f"Current system prompt:\n{seed}\n\n"
        f"Recent step scores:\n{summary}\n"
    )
    try:
        from backend.services.llm_provider import get_chat_provider

        provider = get_chat_provider()
        resp = provider.chat(
            model,
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 800},
        )
        content = ""
        if isinstance(resp, dict):
            content = str((resp.get("message") or {}).get("content") or "")
        else:
            msg = getattr(resp, "message", None)
            content = str(getattr(msg, "content", "") or "")
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```\w*\n?", "", content)
            content = re.sub(r"\n?```$", "", content).strip()
        if len(content) > 80:
            return content[:2000], f"ollama_reflect:{model}"
    except Exception:
        pass

    if dspy is not None:
        # Mark that DSPy is present; still fall back to heuristic for compile without trainset wiring.
        improved, _ = heuristic_improve_prompt(seed, traces)
        return improved, "dspy_available_heuristic"

    improved, _ = heuristic_improve_prompt(seed, traces)
    return improved, "gepa_available_heuristic"


def run_optimize(
    *,
    limit: int = 50,
    apply: bool = False,
    reflection_model: str = "",
) -> Dict[str, Any]:
    traces = load_scored_traces(limit=limit)
    seed = seed_dev_system_prompt()
    mean = mean_trace_score(traces)
    method = "none"
    try:
        optimized, method = try_gepa_optimize(
            seed,
            traces,
            reflection_model=reflection_model,
        )
    except ImportError:
        optimized, method = heuristic_improve_prompt(seed, traces)

    out_path = write_optimized_prompt(optimized)
    applied = False
    if apply:
        apply_dev_system_prompt(optimized)
        applied = True

    return {
        "ok": True,
        "traceCount": len(traces),
        "meanScore": round(mean, 4),
        "method": method,
        "outputPath": str(out_path),
        "applied": applied,
        "seedChars": len(seed),
        "optimizedChars": len(optimized.strip()),
        "note": (
            "Install optional deps: pip install -r requirements-optimize.txt "
            "for GEPA/DSPy; reflection still works via Ollama when available."
        ),
    }
