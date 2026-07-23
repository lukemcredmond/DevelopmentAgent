import os
import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

# Runtime data lives outside the repo (override with ALLHANDS_HOME env var).
# Use allhands_home() when resolving paths at runtime so env changes are respected.
ALLHANDS_HOME = Path(os.environ.get("ALLHANDS_HOME", Path.home() / ".allhands"))
LEGACY_DB_PATH = ROOT_DIR / "scrum_memory.db"
DB_PATH = str(ALLHANDS_HOME / "scrum_memory.db")


def allhands_home() -> Path:
    """Resolve ALLHANDS_HOME from env on each call (tests, custom installs)."""
    return Path(os.environ.get("ALLHANDS_HOME", Path.home() / ".allhands"))


def ensure_allhands_home() -> Path:
    home = allhands_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def diagnostics_dir(project_id: str | None = None) -> Path:
    """Per-project folder under ~/.allhands/diagnostics/."""
    base = ensure_allhands_home() / "diagnostics"
    pid = project_id or "default-proj"
    path = base / pid
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy_database() -> None:
    """Copy scrum_memory.db from repo root into ~/.allhands on first run."""
    ensure_allhands_home()
    legacy = LEGACY_DB_PATH
    target = Path(str(allhands_home() / "scrum_memory.db"))
    if legacy.is_file() and not target.is_file():
        shutil.copy2(legacy, target)

MAX_LOG_ENTRIES = 500
MAX_TASK_DECISIONS = 50
MAX_TASK_TRANSCRIPT = 100
MAX_SPRINT_STEPS = 20
TERMINAL_TIMEOUT_SEC = 120
TEST_TIMEOUT_SEC = 30
LONG_COMMAND_TIMEOUT_SEC = 600

DEFAULT_BOARD = {
    "Backlog": [],
    "In Progress": [],
    "Needs PO": [],
    "Needs User": [],
    "QA": [],
    "Done": [],
}

DEFAULT_VIRTUAL_FS = {
    "package.json": '{\n  "name": "local-scrum-workspace",\n  "version": "1.0.0"\n}'
}

CORS_ORIGINS = [
    "http://127.0.0.1:6767",
    "http://localhost:6767",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
