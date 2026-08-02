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
LLM_DEBUG_LOG: List[Dict[str, Any]] = []

STATE_LOCK = threading.RLock()
ACTIVE_SPRINT_TASK_ID: Optional[str] = None
ACTIVE_SPRINT_AGENT: Optional[str] = None
# When True, agent may keep working on a task already in Done (explicit retry/chat override).
ALLOW_DONE_RETRY: bool = False
ACTIVE_AGENT_RUN: Optional[Any] = None
SPRINT_CANCEL = False
# Discord/UI intent when SPRINT_CANCEL is set: "paused" | "cancelled" | None
SPRINT_CANCEL_INTENT: Optional[str] = None
EVENT_SUBSCRIBERS: List[Any] = []
PENDING_TOOL_REQUESTS: List[Dict[str, Any]] = []
PENDING_TOOL_APPROVALS: List[Any] = []

# Timestamp marking the start of the current sprint agent step (for transcript scoping).
SPRINT_STEP_STARTED_AT: Optional[str] = None
# Monotonic clock at step start (for remaining-step command timeout budget).
SPRINT_STEP_STARTED_MONO: Optional[float] = None

# Current step counters for sprint_progress SSE (set by run_auto_sprint / plan-and-run).
SPRINT_PROGRESS_STEP: int = 0
SPRINT_PROGRESS_MAX: int = 20
SPRINT_NEEDS_USER_COUNT: int = 0

# Outcome of the most recent manual sprint step (API + UI notification).
LAST_STEP_OUTCOME: Optional[Dict[str, Any]] = None
LAST_AGENT_STEP_RESULT: Optional[str] = None
# Progress snapshot when a step hits max LLM iterations (for Extend UX).
LAST_STEP_PROGRESS: Optional[Dict[str, Any]] = None
DEV_STEP_READ_ONLY_NO_EDITS: bool = False
DEV_STEP_INTERRUPTED: bool = False

# Active per-step diagnostics trace (manual sprint steps).
ACTIVE_STEP_DIAGNOSTICS: Optional[Any] = None
LAST_STEP_DIAGNOSTICS: Optional[Dict[str, Any]] = None

REFINEMENT_MODE: bool = False

PROJECT_PLAN_OUTLINE: str = ""

# User-injected workspace-wide command/test output (shared across agents; not tied to one card).
PROJECT_TOOL_EVIDENCE: List[Dict[str, Any]] = []

# Interrupted sprint session context surfaced on startup (crash recovery banner).
RECOVERY_CONTEXT: Optional[Dict[str, Any]] = None

# Offline simulation awaiting user confirm (10s popup in UI).
PENDING_SIMULATION: Optional[Dict[str, Any]] = None

# Primary / backup Ollama model names per agent (primary is what we persist; agent.model may
# temporarily switch to backup during stuck recovery).
PRIMARY_MODELS: Dict[str, str] = {
    "po": "llama3:8b",
    "dev": "qwen2.5-coder:14b",
    "cr": "qwen2.5-coder:7b",
    "qa": "qwen2.5-coder:7b",
}
BACKUP_MODELS: Dict[str, str] = {
    "po": "",
    "dev": "",
    "cr": "",
    "qa": "",
}

# Paths read via read_file during the current sprint agent step (safe_path -> content).
# Tool (name, args_json) keys invoked during the current agent step (cross-step fingerprinting).
STEP_TOOL_FINGERPRINT_KEYS: List[Any] = []
STEP_TOOL_BLOCK_KEYS: List[Any] = []

STEP_FILE_READS: Dict[str, str] = {}

# apply_patch failure counts per path within current sprint step.
STEP_PATCH_FAILURES: Dict[str, int] = {}

# Set during run_fix_verify_loop so SSE agent_run can show fix-verify round.
FIX_VERIFY_ROUND: Optional[int] = None
FIX_VERIFY_MAX_ROUNDS: Optional[int] = None

# Dev focus micro-step prompt rotation (set per sprint step in sprint_service).
SPRINT_PROMPT_ROTATION_ENABLED: bool = False
SPRINT_PROMPT_ROTATION_BLOCKS: List[str] = []
SPRINT_PROMPT_ROTATION_NAMES: List[str] = []
SPRINT_PROMPT_FIXED_PREFIX: str = ""
SPRINT_PROMPT_FIXED_SUFFIX: str = ""

storage = ProjectStorage()
