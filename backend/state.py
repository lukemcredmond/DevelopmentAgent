import threading
from typing import Any, Dict, List, Optional

from backend.config import DEFAULT_BOARD, DEFAULT_VIRTUAL_FS
from backend.storage.project_storage import ProjectStorage

CURRENT_PROJECT_ID = "default-proj"
PROJECT_NAME = "My Local Scrum Project"
PROJECT_BRIEF = "Decompose meal recipe planner modules in Nodejs."
WORKSPACE_DIR = "./workspace"
SKILLS_DIR = "./global_skills"

SHARED_BOARD: Dict[str, List[Dict[str, Any]]] = {k: list(v) for k, v in DEFAULT_BOARD.items()}
VIRTUAL_FILESYSTEM: Dict[str, str] = dict(DEFAULT_VIRTUAL_FS)
SYSTEM_LOGS: List[Dict[str, str]] = []
TOOL_EXECUTION_LOG: List[Dict[str, Any]] = []

STATE_LOCK = threading.RLock()
ACTIVE_SPRINT_TASK_ID: Optional[str] = None
ACTIVE_SPRINT_AGENT: Optional[str] = None
ACTIVE_AGENT_RUN: Optional[Any] = None
SPRINT_CANCEL = False
EVENT_SUBSCRIBERS: List[Any] = []
PENDING_TOOL_REQUESTS: List[Dict[str, Any]] = []
PENDING_TOOL_APPROVALS: List[Any] = []

# Timestamp marking the start of the current sprint agent step (for transcript scoping).
SPRINT_STEP_STARTED_AT: Optional[str] = None

# Current step counters for sprint_progress SSE (set by run_auto_sprint / plan-and-run).
SPRINT_PROGRESS_STEP: int = 0
SPRINT_PROGRESS_MAX: int = 20
SPRINT_NEEDS_USER_COUNT: int = 0

# Paths read via read_file during the current sprint agent step (safe_path -> content).
STEP_FILE_READS: Dict[str, str] = {}

storage = ProjectStorage()
