"""Detect placeholder/incomplete content in workspace files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from backend import state
from backend.services.file_blocker import normalize_file_path
from backend.services.workflow_settings import get_workflow_settings

_WRITE_FILE_ACTIONS = frozenset({"written", "write", "edited"})

# Line-level placeholder patterns (pattern_id, regex)
PLACEHOLDER_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("todo", r"^\s*//\s*TODO\b"),
    ("todo_hash", r"^\s*#\s*TODO\b"),
    ("fixme", r"^\s*//\s*FIXME\b"),
    ("fixme_hash", r"^\s*#\s*FIXME\b"),
    ("auto_scaffold", r"Auto-scaffolded stub"),
    ("this_file_will", r"This file will handle"),
    ("not_implemented", r"not implemented"),
    ("unimplemented", r"throw\s+UnimplementedError"),
    ("pass_only", r"^\s*pass\s*$"),
    ("ellipsis", r"^\s*\.\.\.\s*$"),
    ("stub_function", r"^\s*void\s+\w+Stub\s*\(\s*\)\s*\{\s*\}\s*$"),
)

_SKIP_PATH_SUFFIXES = (
    "_test.dart",
    ".g.dart",
    ".freezed.dart",
)
_SKIP_PATH_PREFIXES = (
    "test/",
    "tests/",
    "node_modules/",
)
_DOC_EXTENSIONS = frozenset({".md", ".txt", ".rst", ".adoc"})

_DELEGATION_LANES = frozenset(
    {
        "Backlog",
        "In Progress",
        "Refinement",
        "Pending Approval",
        "Blocked",
        "Needs PO",
        "Needs User",
        "QA",
        "Code Review",
    }
)


@dataclass
class Finding:
    path: str
    line: int
    pattern_id: str
    message: str


@dataclass
class CompletenessReport:
    findings: List[Finding] = field(default_factory=list)
    blocking_findings: List[Finding] = field(default_factory=list)
    delegated_paths: List[str] = field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking_findings)

    def summary(self, max_items: int = 6) -> str:
        if not self.blocking_findings:
            return ""
        by_path: Dict[str, List[Finding]] = {}
        for f in self.blocking_findings:
            by_path.setdefault(f.path, []).append(f)
        parts: List[str] = []
        for path in sorted(by_path.keys())[:max_items]:
            items = by_path[path]
            labels = sorted({f.pattern_id for f in items})
            line_nums = sorted({f.line for f in items if f.line > 0})
            line_hint = f" lines {', '.join(str(n) for n in line_nums[:4])}" if line_nums else ""
            parts.append(f"{path} ({', '.join(labels)}{line_hint})")
        extra = len(by_path) - max_items
        if extra > 0:
            parts.append(f"+{extra} more")
        return "; ".join(parts)


def _file_ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def should_skip_path(path: str) -> bool:
    norm = normalize_file_path(path)
    if not norm:
        return True
    lower = norm.lower()
    if any(lower.endswith(sfx) for sfx in _SKIP_PATH_SUFFIXES):
        return True
    if any(lower.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
        return True
    if "/node_modules/" in lower:
        return True
    base = os.path.basename(lower)
    if base.startswith("test_") and lower.endswith(".py"):
        return True
    return False


def _strip_comments_and_blank(content: str, ext: str) -> str:
    lines: List[str] = []
    in_block = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ext in (".dart", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cs", ".go"):
            if in_block:
                if "*/" in line:
                    in_block = False
                continue
            if line.startswith("/*"):
                if "*/" not in line:
                    in_block = True
                continue
            if line.startswith("//"):
                continue
            # Strip trailing // comments
            if "//" in line:
                line = line.split("//", 1)[0].strip()
        elif ext == ".py":
            if line.startswith("#"):
                continue
            if '"""' in line or "'''" in line:
                if line.count('"""') == 1 or line.count("'''") == 1:
                    in_block = not in_block
                continue
            if in_block:
                if '"""' in line or "'''" in line:
                    in_block = False
                continue
        elif ext in _DOC_EXTENSIONS:
            return content
        if line:
            lines.append(line)
    return "\n".join(lines)


def is_comment_only_source(content: str, path: str) -> bool:
    ext = _file_ext(path)
    if ext in _DOC_EXTENSIONS:
        return False
    stripped = _strip_comments_and_blank(content, ext)
    if not stripped:
        return True
    # Single import/library line only is still incomplete for implementation files
    code_lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if not code_lines:
        return True
    impl_exts = {".dart", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs", ".go", ".rs"}
    if ext not in impl_exts:
        return False
    # Only import/library/export declarations
    non_decl = [
        ln
        for ln in code_lines
        if not re.match(
            r"^(import|export|library|part|using|package|#include|from\s+\S+\s+import)\b",
            ln,
        )
    ]
    return len(non_decl) == 0


def is_scaffold_stub(content: str) -> bool:
    return "Auto-scaffolded stub" in (content or "")


def _allowlist_patterns(ws: Optional[Dict[str, Any]] = None) -> List[re.Pattern[str]]:
    ws = ws or get_workflow_settings()
    raw = ws.get("placeholderAllowlist") or []
    patterns: List[re.Pattern[str]] = []
    if not isinstance(raw, list):
        return patterns
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            patterns.append(re.compile(text, re.IGNORECASE))
        except re.error:
            continue
    return patterns


def scan_file_content(path: str, content: str, *, ws: Optional[Dict[str, Any]] = None) -> List[Finding]:
    norm = normalize_file_path(path)
    if should_skip_path(norm):
        return []
    ext = _file_ext(norm)
    if ext in _DOC_EXTENSIONS:
        return []

    allowlist = _allowlist_patterns(ws)
    findings: List[Finding] = []
    lines = (content or "").splitlines()

    for idx, line in enumerate(lines, start=1):
        if any(p.search(line) for p in allowlist):
            continue
        for pattern_id, pattern in PLACEHOLDER_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(
                    Finding(
                        path=norm,
                        line=idx,
                        pattern_id=pattern_id,
                        message=f"{pattern_id} at line {idx}",
                    )
                )
                break

    if is_scaffold_stub(content):
        if not any(f.pattern_id == "auto_scaffold" for f in findings):
            findings.append(
                Finding(
                    path=norm,
                    line=1,
                    pattern_id="auto_scaffold",
                    message="auto-scaffold stub marker",
                )
            )

    if is_comment_only_source(content, norm):
        findings.append(
            Finding(
                path=norm,
                line=0,
                pattern_id="comment_only",
                message="comment-only or empty implementation file",
            )
        )

    return findings


def scan_workspace_file(path: str, *, ws: Optional[Dict[str, Any]] = None) -> List[Finding]:
    norm = normalize_file_path(path)
    if not norm or should_skip_path(norm):
        return []
    ws_dir = state.WORKSPACE_DIR
    if not ws_dir:
        return []
    phys = os.path.join(ws_dir, norm.replace("/", os.sep))
    if not os.path.isfile(phys):
        vfs = state.VIRTUAL_FILESYSTEM.get(norm)
        if vfs is None:
            return []
        return scan_file_content(norm, vfs, ws=ws)
    try:
        with open(phys, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    return scan_file_content(norm, content, ws=ws)


def _task_written_paths(task: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    seen: Set[str] = set()
    for entry in task.get("files") or []:
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action") or "").lower()
        if action not in _WRITE_FILE_ACTIONS:
            continue
        path = normalize_file_path(str(entry.get("path") or ""))
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    for p in task.get("writePaths") or []:
        path = normalize_file_path(str(p or ""))
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def task_explicitly_owns_file(task: Dict[str, Any], path: str) -> bool:
    norm = normalize_file_path(path)
    if not norm:
        return False
    tid = str(task.get("id") or "")
    if not tid:
        return False

    lint_src = normalize_file_path(str(task.get("lintSourceFile") or ""))
    if lint_src == norm:
        return True

    title = str(task.get("title") or "")
    if title == f"Lint: {norm}":
        return True
    if title.lower().startswith("implement:"):
        owned = normalize_file_path(title.split(":", 1)[1].strip())
        if owned == norm:
            return True

    for ac in task.get("acceptanceCriteria") or []:
        text = str(ac or "")
        if norm in text or norm.split("/")[-1] in text:
            return True

    for entry in task.get("files") or []:
        if not isinstance(entry, dict):
            continue
        if normalize_file_path(str(entry.get("path") or "")) == norm:
            action = str(entry.get("action") or "").lower()
            if action in _WRITE_FILE_ACTIONS:
                return True

    for p in task.get("writePaths") or []:
        if normalize_file_path(str(p or "")) == norm:
            return True

    return False


def find_delegated_owner(path: str, exclude_task_id: str) -> Optional[Dict[str, Any]]:
    """Another open card that explicitly owns this file path."""
    norm = normalize_file_path(path)
    exclude = str(exclude_task_id or "")
    if not norm:
        return None
    for lane, tasks in (state.SHARED_BOARD or {}).items():
        if lane not in _DELEGATION_LANES:
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            if not tid or tid == exclude:
                continue
            if task_explicitly_owns_file(task, norm):
                return task
    return None


def _delegation_allowed(
    task: Dict[str, Any],
    path: str,
    findings: Sequence[Finding],
    ws: Dict[str, Any],
) -> bool:
    if not ws.get("allowStubDelegation"):
        return False
    if not findings:
        return False
    scaffolded = {normalize_file_path(p) for p in (task.get("scaffoldedFiles") or [])}
    norm = normalize_file_path(path)
    if norm not in scaffolded and not any(f.pattern_id == "auto_scaffold" for f in findings):
        return False
    owner = find_delegated_owner(norm, str(task.get("id") or ""))
    return owner is not None


def scan_task_files(
    task: Dict[str, Any],
    *,
    ws: Optional[Dict[str, Any]] = None,
) -> CompletenessReport:
    ws = ws or get_workflow_settings()
    report = CompletenessReport()
    paths = _task_written_paths(task)
    if not paths:
        return report

    all_findings: List[Finding] = []
    for path in paths:
        findings = scan_workspace_file(path, ws=ws)
        if findings:
            all_findings.extend(findings)

    report.findings = all_findings
    blocking: List[Finding] = []
    delegated: List[str] = []

    by_path: Dict[str, List[Finding]] = {}
    for f in all_findings:
        by_path.setdefault(f.path, []).append(f)

    for path, findings in by_path.items():
        if _delegation_allowed(task, path, findings, ws):
            delegated.append(path)
            continue
        blocking.extend(findings)

    report.blocking_findings = blocking
    report.delegated_paths = delegated
    return report


def completeness_gate_blocks(
    task: Dict[str, Any],
    *,
    ws: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    ws = ws or get_workflow_settings()
    try:
        from backend.services.implementer_profile import implementer_gate_relaxation_active

        if implementer_gate_relaxation_active(ws):
            return False, ""
    except Exception:
        pass
    if not ws.get("requireFileCompleteness", True):
        return False, ""
    report = scan_task_files(task, ws=ws)
    if not report.has_blocking:
        return False, ""
    return True, f"Incomplete files: {report.summary()}"


def format_write_warning(findings: Sequence[Finding]) -> str:
    if not findings:
        return ""
    line_nums = sorted({f.line for f in findings if f.line > 0})
    labels = sorted({f.pattern_id for f in findings})
    line_hint = ", ".join(str(n) for n in line_nums[:6])
    label_hint = ", ".join(labels[:6])
    return (
        f"Warning: file contains placeholder/incomplete patterns"
        f" (lines {line_hint}): {label_hint}. "
        "Replace with full implementation before moving to QA."
    )


def record_task_completeness_warnings(task_id: str, path: str, findings: Sequence[Finding]) -> None:
    if not task_id or not findings:
        return
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(task_id)
    if not task:
        return
    warnings = task.setdefault("fileCompletenessWarnings", {})
    if not isinstance(warnings, dict):
        warnings = {}
        task["fileCompletenessWarnings"] = warnings
    warnings[normalize_file_path(path)] = [
        {"line": f.line, "patternId": f.pattern_id, "message": f.message} for f in findings
    ]
