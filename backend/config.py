import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
DB_PATH = str(ROOT_DIR / "scrum_memory.db")

MAX_LOG_ENTRIES = 500
MAX_TASK_DECISIONS = 50
MAX_TASK_TRANSCRIPT = 100
MAX_SPRINT_STEPS = 20
TERMINAL_TIMEOUT_SEC = 30
TEST_TIMEOUT_SEC = 30

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
