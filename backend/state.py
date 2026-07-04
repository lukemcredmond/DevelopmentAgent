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

STATE_LOCK = threading.Lock()
ACTIVE_SPRINT_TASK_ID: Optional[str] = None
ACTIVE_SPRINT_AGENT: Optional[str] = None
SPRINT_CANCEL = False
EVENT_SUBSCRIBERS: List[Any] = []

storage = ProjectStorage()
