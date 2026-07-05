"""Parse structured diagnostics from analyze/lint command output."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def parse_flutter_analyze(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    # info • message • file:line:col
    pattern = re.compile(
        r"^\s*(error|warning|info)\s+•\s+(.+?)\s+•\s+(.+?):(\d+):(\d+)\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in pattern.finditer(output):
        findings.append(
            {
                "severity": m.group(1).lower(),
                "message": m.group(2).strip(),
                "file": m.group(3).strip().replace("\\", "/"),
                "line": int(m.group(4)),
                "column": int(m.group(5)),
            }
        )
    return findings


def parse_tsc_output(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$", re.MULTILINE)
    for m in pattern.finditer(output):
        findings.append(
            {
                "severity": m.group(4).lower(),
                "message": f"{m.group(5)}: {m.group(6).strip()}",
                "file": m.group(1).strip().replace("\\", "/"),
                "line": int(m.group(2)),
                "column": int(m.group(3)),
            }
        )
    return findings


def parse_eslint_output(output: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    pattern = re.compile(r"^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)\s+(\S+)$", re.MULTILINE)
    for m in pattern.finditer(output):
        findings.append(
            {
                "severity": m.group(3).lower(),
                "message": m.group(4).strip(),
                "file": m.group(5).strip().replace("\\", "/"),
                "line": int(m.group(1)),
                "column": int(m.group(2)),
            }
        )
    return findings


def parse_command_diagnostics(command: str, output: str) -> List[Dict[str, Any]]:
    cmd = (command or "").lower()
    out = output or ""
    if "flutter analyze" in cmd or "dart analyze" in cmd:
        return parse_flutter_analyze(out)
    if "tsc" in cmd or "typescript" in cmd:
        return parse_tsc_output(out)
    if "eslint" in cmd:
        return parse_eslint_output(out)
    if "error •" in out or "warning •" in out:
        return parse_flutter_analyze(out)
    return []


def parse_diagnostics_from_transcript(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    all_findings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in reversed(transcript or []):
        if entry.get("toolName") != "run_command":
            continue
        cmd = str((entry.get("toolArgs") or {}).get("command") or "")
        out = str(entry.get("toolOutput") or entry.get("content") or "")
        for f in parse_command_diagnostics(cmd, out):
            key = f"{f.get('file')}:{f.get('line')}:{f.get('message')}"
            if key not in seen:
                seen.add(key)
                all_findings.append(f)
    return all_findings
