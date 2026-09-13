"""Per-card filesystem ledger (bounded plan/notes next-work).

State lives under ``{workspace}/docs/tasks/{task_id}/`` (same folder as the spec)
so AllHands, Cursor, and other tools share one markdown working set.
Legacy ``.allhands/cards/{task_id}/`` is read and copied on first access.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.task_docs import (
    TASKS_PREFIX,
    legacy_ledger_dir,
    migrate_legacy_task_docs,
    safe_task_id,
)
from backend.services.workflow_settings import get_workflow_settings

LEDGER_REL = Path(TASKS_PREFIX)
MAX_PLAN_CHARS = 4000
MAX_NOTES_CHARS = 8000
MAX_ORACLE_CHARS = 2000
MAX_TASKS = 12
MAX_TASK_MD_CHARS = 6000

_WS_RE = re.compile(r"\s+")
_PLAN_ITEM_RE = re.compile(r"^[-*]\s+\[([^\]]+)\]\s+(.*\S)")

LENGTH_CUTOFF_REASONS = frozenset(
    {"length", "max_tokens", "max_length", "max_output_tokens"}
)

IDEATION_SYSTEM = (
    "You are the FIRST WORKER on this card. Do NOT write code and do NOT call tools. "
    "Identify the core difficulty, list several DISTINCT approaches (not variations of one idea), "
    "and note pitfalls for each. Prose only — no code blocks. "
    "Respond with EXACTLY:\n### NOTES\n<findings>\n### NEXT\n- <concrete next step>"
)

CUTOFF_SYSTEM = (
    "A worker attempt was CUT OFF when it hit the token limit. Summarize the partial attempt "
    "in 3-5 sentences: which approach it was pursuing, what it established or ruled out, "
    "how far it got, and what remained unfinished. Be concrete so another step can resume. "
    "Do NOT try to finish the solution yourself."
)


def ledger_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableCardLedger", True))


def ideation_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableDevIdeation", True)) and ledger_enabled(ws)


def oracle_override_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableOracleDoneOverride", True))


def cutoff_summarizer_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableCutoffSummarizer", True)) and ledger_enabled(ws)


def same_next_task_stop_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableSameNextTaskStop", True))


def _workspace_root() -> Optional[Path]:
    from backend import state

    raw = str(getattr(state, "WORKSPACE_DIR", "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def ledger_dir(task_id: str) -> Optional[Path]:
    root = _workspace_root()
    if root is None:
        return None
    return root / LEDGER_REL / safe_task_id(task_id)


def _read(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)


def read_file(task_id: str, name: str) -> str:
    migrate_legacy_task_docs(task_id)
    folder = ledger_dir(task_id)
    if folder is not None:
        text = _read(folder / name)
        if text:
            return text
    old = legacy_ledger_dir(task_id)
    if old is not None:
        return _read(old / name)
    return ""


def write_file(task_id: str, name: str, content: str, *, cap: int = MAX_NOTES_CHARS) -> None:
    migrate_legacy_task_docs(task_id)
    folder = ledger_dir(task_id)
    if folder is None:
        return
    _write(folder / name, (content or "")[: max(0, cap)])


def append_notes(task_id: str, heading: str, body: str) -> None:
    migrate_legacy_task_docs(task_id)
    folder = ledger_dir(task_id)
    if folder is None:
        return
    text = (body or "").strip()
    if not text:
        return
    heading = (heading or "note").strip() or "note"
    block = f"\n## {heading}\n{text[: MAX_NOTES_CHARS]}\n"
    _append(folder / "notes.md", block)
    _cap_notes(task_id)


def _cap_notes(task_id: str) -> None:
    notes = read_file(task_id, "notes.md")
    if len(notes) > MAX_NOTES_CHARS:
        write_file(task_id, "notes.md", notes[-MAX_NOTES_CHARS :], cap=MAX_NOTES_CHARS)


def overwrite_notes(task_id: str, body: str) -> None:
    write_file(task_id, "notes.md", (body or "").strip()[:MAX_NOTES_CHARS], cap=MAX_NOTES_CHARS)


def notes_empty(task_id: str) -> bool:
    return not read_file(task_id, "notes.md").strip()


def write_plan(task_id: str, plan: str) -> None:
    write_file(task_id, "plan.md", (plan or "").strip(), cap=MAX_PLAN_CHARS)


def write_oracle(task_id: str, *, passed: bool, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    payload = f"{status}\n{(detail or '').strip()[: MAX_ORACLE_CHARS - 8]}"
    write_file(task_id, "last_oracle.txt", payload, cap=MAX_ORACLE_CHARS)
    append_notes(task_id, "oracle", f"{status}: {(detail or '').strip()[:800]}")
    try:
        from backend.agents.task_context import find_task_by_id

        task = find_task_by_id(str(task_id))
        if isinstance(task, dict):
            task["lastOraclePassed"] = bool(passed)
            task["lastOracleDetail"] = (detail or "")[:400]
    except Exception:
        pass


def last_oracle_passed(task_id: str, task: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    raw = read_file(task_id, "last_oracle.txt").strip()
    if raw:
        first = raw.splitlines()[0].strip().upper()
        if first.startswith("PASS"):
            return True
        if first.startswith("FAIL"):
            return False
    if isinstance(task, dict) and "lastOraclePassed" in task:
        return bool(task.get("lastOraclePassed"))
    return None


def oracle_blocks_done(task: Dict[str, Any], ws: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    """True when a failing lint/test oracle must veto Done / QA advance."""
    if not oracle_override_enabled(ws):
        return False, ""
    if not isinstance(task, dict):
        return False, ""
    if (task.get("qaEvidence") or {}).get("userOverride"):
        return False, ""
    tid = str(task.get("id") or "")
    passed = last_oracle_passed(tid, task)
    if passed is False:
        detail = (read_file(tid, "last_oracle.txt") or str(task.get("lastOracleDetail") or "")).strip()
        snippet = " ".join(detail.splitlines()[:3])[:240]
        return True, f"Oracle FAIL — cannot mark Done until tests/lint pass. {snippet}"
    return False, ""


def _tasks_from_json(raw: str) -> List[Dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data[:MAX_TASKS]:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("desc") or "").strip()
        if not desc:
            continue
        out.append(
            {
                "id": int(item.get("id") or len(out) + 1),
                "desc": desc[:400],
                "status": str(item.get("status") or "pending"),
                "result": str(item.get("result") or "")[:400],
            }
        )
    return out


def _parse_plan_tasks(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        match = _PLAN_ITEM_RE.match(line.strip())
        if not match:
            continue
        mark = match.group(1).strip().lower()
        rest = match.group(2).strip()
        desc, result = rest, ""
        if " — " in rest:
            desc, result = rest.split(" — ", 1)
        elif " -- " in rest:
            desc, result = rest.split(" -- ", 1)
        desc = desc.strip()
        if not desc:
            continue
        status = "done" if mark in {"x", "done"} else "pending"
        out.append(
            {
                "id": len(out) + 1,
                "desc": desc[:400],
                "status": status,
                "result": result.strip()[:400],
            }
        )
        if len(out) >= MAX_TASKS:
            break
    return out


def _format_plan_tasks(tasks: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in tasks[:MAX_TASKS]:
        mark = "x" if str(item.get("status") or "") == "done" else " "
        extra = f" — {item['result']}" if item.get("result") else ""
        desc = str(item.get("desc") or "").strip()
        if not desc:
            continue
        lines.append(f"- [{mark}] {desc}{extra}")
    return ("\n".join(lines) + "\n") if lines else ""


def load_tasks(task_id: str) -> List[Dict[str, Any]]:
    parsed = _parse_plan_tasks(read_file(task_id, "plan.md"))
    if parsed:
        return parsed
    from_json = _tasks_from_json(read_file(task_id, "tasks.json"))
    if from_json:
        return from_json
    old = legacy_ledger_dir(task_id)
    if old is not None:
        return _tasks_from_json(_read(old / "tasks.json"))
    return []


def save_tasks(task_id: str, tasks: List[Dict[str, Any]]) -> None:
    existing = read_file(task_id, "plan.md")
    prose = [
        line
        for line in existing.splitlines()
        if line.strip() and not _PLAN_ITEM_RE.match(line.strip())
    ]
    checklists = _format_plan_tasks(tasks).rstrip()
    parts = []
    if prose:
        parts.append("\n".join(prose).strip())
    if checklists:
        parts.append(checklists)
    write_file(task_id, "plan.md", ("\n\n".join(parts) + "\n") if parts else "", cap=MAX_PLAN_CHARS)


def seed_ledger_from_task(task: Dict[str, Any]) -> None:
    """Create plan/tasks/task.md from the card if missing. Safe to call every visit."""
    if not ledger_enabled() or not isinstance(task, dict):
        return
    tid = str(task.get("id") or "")
    if not tid or ledger_dir(tid) is None:
        return
    title = str(task.get("title") or "").strip()
    desc = str(task.get("description") or "").strip()
    acs = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if not read_file(tid, "task.md").strip():
        body = f"# {title}\n\n{desc}\n"
        if acs:
            body += "\n## Acceptance criteria\n" + "\n".join(f"- {c}" for c in acs) + "\n"
        write_file(tid, "task.md", body, cap=MAX_TASK_MD_CHARS)
    if not load_tasks(tid) and acs:
        save_tasks(
            tid,
            [
                {"id": i + 1, "desc": ac[:400], "status": "pending", "result": ""}
                for i, ac in enumerate(acs[:MAX_TASKS])
            ],
        )
    if not read_file(tid, "plan.md").strip() and (desc or title):
        write_plan(tid, (desc or title)[:MAX_PLAN_CHARS])


def format_ledger_for_prompt(task: Dict[str, Any], *, max_chars: int = 6000) -> str:
    if not ledger_enabled() or not isinstance(task, dict):
        return ""
    tid = str(task.get("id") or "")
    if not tid:
        return ""
    seed_ledger_from_task(task)
    plan = read_file(tid, "plan.md").strip()[:MAX_PLAN_CHARS]
    notes = read_file(tid, "notes.md").strip()[-MAX_NOTES_CHARS:]
    oracle = read_file(tid, "last_oracle.txt").strip()[:MAX_ORACLE_CHARS]
    tasks = load_tasks(tid)
    if not plan and not notes and not oracle and not tasks:
        return ""
    lines = [
        "=== CARD LEDGER (bounded working set — prefer over long transcript) ===",
        "Treat plan/notes/oracle as current truth for this card. Do not redo settled approaches.",
    ]
    if plan:
        lines.append("PLAN:\n" + plan)
    if tasks:
        lines.append("TASKS:")
        for item in tasks:
            mark = "done" if str(item.get("status")) == "done" else "todo"
            extra = f" — {item['result']}" if item.get("result") else ""
            lines.append(f"- [{mark}] {item.get('desc', '')}{extra}")
    if notes:
        lines.append("NOTES:\n" + notes)
    if oracle:
        lines.append("ORACLE (subprocess lint/test — ground truth):\n" + oracle)
        if oracle.upper().startswith("FAIL"):
            lines.append(
                "A failing oracle OVERRIDES any claim that the card is done. "
                "Fix the failing case or switch approach."
            )
    text = "\n".join(lines).strip() + "\n"
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def _task_has_writes(task: Dict[str, Any]) -> bool:
    files = task.get("files") or []
    if not files:
        return False
    for item in files:
        if isinstance(item, str) and item.strip():
            return True
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            action = str(item.get("action") or "touched").lower()
            if action in ("write", "written", "create", "created", "patch", "patched", "touched"):
                return True
            return True
    return False


def should_run_ideation(task: Dict[str, Any], ws: Optional[Dict[str, Any]] = None) -> bool:
    if not ideation_enabled(ws) or not isinstance(task, dict):
        return False
    if task.get("ideationDone"):
        return False
    tid = str(task.get("id") or "")
    if not tid:
        return False
    seed_ledger_from_task(task)
    if not notes_empty(tid):
        return False
    if _task_has_writes(task):
        return False
    return True


def parse_ideation_reply(text: str) -> tuple[str, List[str]]:
    raw = (text or "").strip()
    notes = raw
    nexts: List[str] = []
    m_notes = re.search(r"###\s*NOTES\s*\n(.*?)(?=\n###\s*NEXT|\Z)", raw, re.I | re.S)
    m_next = re.search(r"###\s*NEXT\s*\n(.*)", raw, re.I | re.S)
    if m_notes:
        notes = m_notes.group(1).strip()
    if m_next:
        for line in m_next.group(1).splitlines():
            m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)", line)
            if m:
                nexts.append(m.group(1).strip())
    notes = re.sub(r"```.*?```", "[code omitted]", notes, flags=re.S)
    return notes[: MAX_NOTES_CHARS], nexts[:MAX_TASKS]


def apply_ideation_result(task: Dict[str, Any], reply: str) -> None:
    tid = str(task.get("id") or "")
    notes, nexts = parse_ideation_reply(reply)
    if notes:
        append_notes(tid, "ideation", notes)
    existing = load_tasks(tid)
    have = {str(t.get("desc") or "").lower() for t in existing}
    nid = max([int(t.get("id") or 0) for t in existing], default=0)
    for desc in nexts:
        if desc.lower() not in have and len(existing) < MAX_TASKS:
            nid += 1
            existing.append({"id": nid, "desc": desc[:400], "status": "pending", "result": ""})
            have.add(desc.lower())
    if existing:
        save_tasks(tid, existing)
    task["ideationDone"] = True


def is_length_cutoff(done_reason: Optional[str]) -> bool:
    raw = str(done_reason or "").strip().lower()
    if not raw:
        return False
    if raw in LENGTH_CUTOFF_REASONS:
        return True
    return "length" in raw and "empty" not in raw


def next_work_key(task: Dict[str, Any]) -> str:
    """Normalized 'next unit of work' for the same-task no-progress guard."""
    if not isinstance(task, dict):
        return ""
    try:
        from backend.services.focus_slice import _focus_summary_line

        line = _focus_summary_line(task) or ""
    except Exception:
        line = ""
    if not line:
        tid = str(task.get("id") or "")
        pending = [t for t in load_tasks(tid) if str(t.get("status")) != "done"]
        if pending:
            line = str(pending[0].get("desc") or "")
        else:
            line = str(task.get("title") or "")
    return _WS_RE.sub(" ", line.strip().lower())[:200]


def record_next_work_visit(
    task: Dict[str, Any],
    *,
    writes_succeeded: int = 0,
    oracle_passed: Optional[bool] = None,
) -> None:
    if not isinstance(task, dict):
        return
    key = next_work_key(task)
    progress = int(writes_succeeded or 0) > 0 or oracle_passed is True
    task["lastNextWorkKey"] = key
    task["lastNextWorkNoWrite"] = not progress


def same_next_task_should_park(
    task: Dict[str, Any], ws: Optional[Dict[str, Any]] = None
) -> bool:
    if not same_next_task_stop_enabled(ws) or not isinstance(task, dict):
        return False
    key = next_work_key(task)
    prev = str(task.get("lastNextWorkKey") or "")
    if not key or not prev or key != prev:
        return False
    return bool(task.get("lastNextWorkNoWrite"))


def run_dev_ideation(agent: Any, task: Dict[str, Any]) -> str:
    """One no-code brainstorm call. Returns notes text or empty on skip/fail."""
    if not should_run_ideation(task):
        return ""
    tid = str(task.get("id") or "")
    seed_ledger_from_task(task)
    plan = read_file(tid, "plan.md")
    problem = read_file(tid, "task.md") or str(task.get("description") or task.get("title") or "")
    user = f"PROBLEM:\n{problem[:4000]}\n\nPLAN:\n{plan[:MAX_PLAN_CHARS]}"
    try:
        messages = [
            {"role": "system", "content": IDEATION_SYSTEM},
            {"role": "user", "content": user},
        ]
        prev = getattr(agent, "_skip_cutoff_summary", False)
        agent._skip_cutoff_summary = True
        try:
            response = agent._chat(messages, tools=None, iteration=0, task_id=tid)
        finally:
            agent._skip_cutoff_summary = prev
        if response is None:
            return ""
        content = getattr(getattr(response, "message", None), "content", None) or ""
        apply_ideation_result(task, content)
        from backend.services.logs import add_system_log

        add_system_log("Developer", "info", f"{tid}: ideation notes written (no code)")
        return content
    except Exception:
        return ""


def summarize_truncated_generation(
    agent: Any,
    task: Dict[str, Any],
    *,
    partial_text: str,
    done_reason: str = "length",
) -> str:
    """Short salvage call after a non-empty length cutoff. Never used for empty gens."""
    if getattr(agent, "_skip_cutoff_summary", False):
        return ""
    if getattr(agent, "_cutoff_summarized", False):
        return ""
    if not cutoff_summarizer_enabled():
        return ""
    text = (partial_text or "").strip()
    if not text or not is_length_cutoff(done_reason):
        return ""
    if not isinstance(task, dict):
        return ""
    tid = str(task.get("id") or "")
    snippet = text if len(text) <= 9000 else text[:3500] + "\n...[middle omitted]...\n" + text[-5500:]
    desc = next_work_key(task) or str(task.get("title") or "")
    user = f"TASK: {desc}\n\nCUT-OFF ATTEMPT:\n{snippet}"
    try:
        messages = [
            {"role": "system", "content": CUTOFF_SYSTEM},
            {"role": "user", "content": user},
        ]
        agent._skip_cutoff_summary = True
        agent._cutoff_summarized = True
        response = agent._chat(messages, tools=None, iteration=0, task_id=tid)
        content = getattr(getattr(response, "message", None), "content", None) or ""
        digest = (content or "").strip()[:1500]
        if digest:
            append_notes(tid, "cutoff", digest)
            from backend.services.logs import add_system_log

            add_system_log(
                getattr(agent, "role", "Developer"),
                "info",
                f"{tid}: cutoff summarizer salvaged truncated generation",
            )
        return digest
    except Exception:
        return ""
    finally:
        try:
            agent._skip_cutoff_summary = False
        except Exception:
            pass


def maybe_record_tool_oracle(
    task: Optional[Dict[str, Any]],
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_output: str,
    success: bool,
) -> None:
    if not isinstance(task, dict) or not oracle_override_enabled():
        return
    name = str(tool_name or "")
    cmd = str((arguments or {}).get("command") or "").lower()
    is_test = name == "run_test" or (
        name == "run_command"
        and any(
            tok in cmd
            for tok in (
                "pytest",
                "npm test",
                "npm run test",
                "dotnet test",
                "flutter test",
                "cargo test",
                "go test",
                "ruff check",
                "analyze",
            )
        )
    )
    if not is_test:
        return
    tid = str(task.get("id") or "")
    if not tid:
        return
    snippet = (tool_output or "").replace("\n", " ")[:500]
    write_oracle(tid, passed=bool(success), detail=f"{name} {cmd[:80]} {snippet}".strip())
