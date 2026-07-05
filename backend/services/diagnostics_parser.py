"""Parse structured diagnostics from analyze/lint command output."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def _dedupe_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for finding in findings:
        key = (
            f"{finding.get('file')}:{finding.get('line')}:{finding.get('column')}:"
            f"{finding.get('message')}"
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def parse_flutter_analyze(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+•\s+(.+?)\s+•\s+(.+?):(\d+):(\d+)\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in pattern.finditer(output):
        findings.append(
            {
                "severity": match.group(1).lower(),
                "message": match.group(2).strip(),
                "file": match.group(3).strip().replace("\\", "/"),
                "line": int(match.group(4)),
                "column": int(match.group(5)),
            }
        )
    return findings


def parse_flutter_analyze_dash(output: str) -> List[Dict[str, Any]]:
    """Flutter/Dart analyzer lines using dash separators."""
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+-\s+(.+?)\s+-\s+(.+?):(\d+):(\d+)\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in pattern.finditer(output):
        findings.append(
            {
                "severity": match.group(1).lower(),
                "message": match.group(2).strip(),
                "file": match.group(3).strip().replace("\\", "/"),
                "line": int(match.group(4)),
                "column": int(match.group(5)),
            }
        )
    return findings


def parse_dart_analyze(output: str) -> List[Dict[str, Any]]:
    return _dedupe_findings(parse_flutter_analyze(output) + parse_flutter_analyze_dash(output))


def parse_tsc_output(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        findings.append(
            {
                "severity": match.group(4).lower(),
                "message": f"{match.group(5)}: {match.group(6).strip()}",
                "file": match.group(1).strip().replace("\\", "/"),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
            }
        )
    return findings


def parse_eslint_output(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)\s+(\S+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        findings.append(
            {
                "severity": match.group(3).lower(),
                "message": match.group(4).strip(),
                "file": match.group(5).strip().replace("\\", "/"),
                "line": int(match.group(1)),
                "column": int(match.group(2)),
            }
        )
    return findings


def parse_pytest_failures(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(r"^FAILED\s+(\S+(?:::\S+)?)\s*[-–]?\s*(.*)$", re.MULTILINE)
    for match in pattern.finditer(output):
        target = match.group(1).strip()
        message = match.group(2).strip() or "Test failed"
        file_part = target.split("::")[0]
        findings.append(
            {
                "severity": "error",
                "message": f"{target}: {message}" if message else target,
                "file": file_part.replace("\\", "/"),
                "line": 0,
                "column": 0,
            }
        )
    return findings


def parse_generic(output: str) -> List[Dict[str, Any]]:
    """Fallback parsers for common file:line formats."""
    findings: List[Dict[str, Any]] = []

    path_line_col = re.compile(
        r"^(.+?):(\d+):(\d+)[:\s]+(error|warning|info)?\s*(.+)$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in path_line_col.finditer(output):
        severity = (match.group(4) or "error").lower()
        findings.append(
            {
                "severity": severity,
                "message": match.group(5).strip(),
                "file": match.group(1).strip().replace("\\", "/"),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
            }
        )

    paren_form = re.compile(
        r"^(.+?)\((\d+),(\d+)\):\s+(error|warning|info)?\s*(.+)$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in paren_form.finditer(output):
        severity = (match.group(4) or "error").lower()
        findings.append(
            {
                "severity": severity,
                "message": match.group(5).strip(),
                "file": match.group(1).strip().replace("\\", "/"),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
            }
        )

    gh_actions = re.compile(
        r"^##\[(error|warning)\]\s*(.+?)(?:\s+(.+?)\((\d+),(\d+)\))?\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in gh_actions.finditer(output):
        file_path = (match.group(3) or "").strip().replace("\\", "/")
        findings.append(
            {
                "severity": match.group(1).lower(),
                "message": match.group(2).strip(),
                "file": file_path or "?",
                "line": int(match.group(4)) if match.group(4) else 0,
                "column": int(match.group(5)) if match.group(5) else 0,
            }
        )

    return findings


def summarize_diagnostics(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return ""
    errors = sum(1 for item in findings if item.get("severity") == "error")
    warnings = sum(1 for item in findings if item.get("severity") == "warning")
    files = len({item.get("file") for item in findings if item.get("file")})
    parts: List[str] = []
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if not parts:
        count = len(findings)
        summary = f"{count} finding{'s' if count != 1 else ''}"
    else:
        summary = ", ".join(parts)
    if files:
        summary += f" in {files} file{'s' if files != 1 else ''}"
    return summary


def parse_command_diagnostics(command: str, output: str) -> List[Dict[str, Any]]:
    cmd = (command or "").lower()
    out = output or ""
    findings: List[Dict[str, Any]] = []

    if "flutter analyze" in cmd or "dart analyze" in cmd:
        findings.extend(parse_dart_analyze(out))
    elif "tsc" in cmd or "typescript" in cmd:
        findings.extend(parse_tsc_output(out))
    elif "eslint" in cmd:
        findings.extend(parse_eslint_output(out))
    elif "pytest" in cmd:
        findings.extend(parse_pytest_failures(out))
    else:
        if "error •" in out or "warning •" in out or "info •" in out:
            findings.extend(parse_dart_analyze(out))
        if "FAILED " in out:
            findings.extend(parse_pytest_failures(out))

    if not findings or "ruff" in cmd or " pylint" in cmd or "mypy" in cmd:
        generic = parse_generic(out)
        if generic:
            findings.extend(generic)

    return _dedupe_findings(findings)


def parse_diagnostics_from_transcript(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    all_findings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in reversed(transcript or []):
        if entry.get("toolName") != "run_command":
            continue
        cmd = str((entry.get("toolArgs") or {}).get("command") or "")
        out = str(entry.get("toolOutput") or entry.get("content") or "")
        for finding in parse_command_diagnostics(cmd, out):
            key = f"{finding.get('file')}:{finding.get('line')}:{finding.get('message')}"
            if key not in seen:
                seen.add(key)
                all_findings.append(finding)
    return all_findings
