"""Process-level build metadata for diagnostics and support bundles."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

DIAGNOSTICS_SCHEMA_VERSION = 2

RECOVERY_FEATURES: List[str] = [
    "lint_wall_recovery",
    "synthetic_read_continue",
    "step_stall_watchdog",
    "slim_dev_prompt",
    "backup_model_pinned",
    "diagnostics_build_stamp",
    "phase3_latch_dedupe",
    "needs_user_cap_exempt",
    "dev_backup_guard",
    "implementer_stop_on_latch",
    "phase4_composer_dev_step",
    "phase4_text_rejection_park",
    "phase4_sprint_work_latch",
    "dev_refusal_triage",
    "dev_step_wall_intrastep",
    "needs_user_hash_reset_on_defer",
    "patch_write_overwrite_recovery",
    "forced_patch_read_exception",
    "meta_refusal_class",
    "sprint_diagnostics_rollup",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_app_build_info() -> Dict[str, Any]:
    """Cached at first call (process lifetime)."""
    git_sha = "unknown"
    git_sha_full = ""
    try:
        root = _repo_root()
        def _git_line(args: List[str]) -> str:
            raw = subprocess.check_output(
                args,
                cwd=str(root),
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace").strip()
            return str(raw).strip()

        git_sha_full = _git_line(["git", "rev-parse", "HEAD"])
        git_sha = _git_line(["git", "rev-parse", "--short", "HEAD"])
    except Exception:
        pass
    return {
        "diagnosticsSchemaVersion": DIAGNOSTICS_SCHEMA_VERSION,
        "gitSha": git_sha,
        "gitShaFull": git_sha_full or git_sha,
        "recoveryFeatures": list(RECOVERY_FEATURES),
    }


def diagnostics_schema_version() -> int:
    return DIAGNOSTICS_SCHEMA_VERSION
