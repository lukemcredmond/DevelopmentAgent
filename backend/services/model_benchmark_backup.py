"""Rotating JSON file backups for model benchmark snapshots."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import allhands_home

logger = logging.getLogger(__name__)

BACKUP_RETENTION_PER_URL = 30
BENCHMARK_BACKUP_DIR = "benchmark-backups"


def _backup_root() -> Path:
    root = allhands_home() / BENCHMARK_BACKUP_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def url_to_backup_slug(url: str) -> str:
    raw = (url or "default").strip().rstrip("/").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw.replace("://", "-"))
    slug = slug.strip("-") or "default"
    return slug[:80]


def write_benchmark_backup(
    snapshot: Dict[str, Any],
    url: str,
    reason: str = "persist",
) -> Optional[Path]:
    """Write snapshot JSON; trim old files for this URL. Never raises."""
    try:
        slug = url_to_backup_slug(url)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_reason = re.sub(r"[^a-z0-9_-]", "", (reason or "persist").lower())[:24]
        path = _backup_root() / f"{slug}-{ts}-{safe_reason}.json"
        payload = {**snapshot, "_backupMeta": {"url": url, "reason": reason, "writtenAt": ts}}
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        _trim_backups(slug)
        return path
    except Exception as exc:
        logger.warning("benchmark file backup failed: %s", exc)
        return None


def _trim_backups(slug: str) -> None:
    root = _backup_root()
    pattern = f"{slug}-*.json"
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[BACKUP_RETENTION_PER_URL:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass


def list_benchmark_backups(url: str, limit: int = 20) -> List[Dict[str, Any]]:
    slug = url_to_backup_slug(url)
    root = _backup_root()
    files = sorted(root.glob(f"{slug}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for path in files[:limit]:
        try:
            st = path.stat()
            out.append(
                {
                    "filename": path.name,
                    "sizeBytes": st.st_size,
                    "modifiedAt": int(st.st_mtime),
                }
            )
        except OSError:
            continue
    return out


def _safe_backup_path(filename: str) -> Optional[Path]:
    name = (filename or "").strip().replace("\\", "/").split("/")[-1]
    if not name or ".." in name or not name.endswith(".json"):
        return None
    path = (_backup_root() / name).resolve()
    root = _backup_root().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def load_backup_file(filename: str) -> Optional[Dict[str, Any]]:
    path = _safe_backup_path(filename)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop("_backupMeta", None)
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("benchmark backup read failed %s: %s", filename, exc)
    return None


def load_latest_backup(url: str) -> Optional[Dict[str, Any]]:
    items = list_benchmark_backups(url, limit=1)
    if not items:
        return None
    return load_backup_file(str(items[0].get("filename") or ""))
