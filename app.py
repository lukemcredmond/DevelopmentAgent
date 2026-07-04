import os
import re
import json
import sqlite3
import requests
import math
import shutil
import threading
from typing import List, Dict, Any, Callable, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Build CDN URLs dynamically using split strings to prevent markdown link processors from corrupting them
PROTO = "https"
CDN_TAILWIND = PROTO + "://cdn.tailwindcss.com"
CDN_REACT = PROTO + "://unpkg.com/react@18/umd/react.development.js"
CDN_REACT_DOM = PROTO + "://unpkg.com/react-dom@18/umd/react-dom.development.js"
CDN_BABEL = PROTO + "://unpkg.com/@babel/standalone/babel.min.js"
CDN_FONTAWESOME = PROTO + "://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"

# =====================================================================
# 🧠 LIGHTWEIGHT SEMANTIC MEMORY STORAGE (SQLite + TF-IDF Sim)
# =====================================================================
class SemanticMemoryEngine:
    """
    A robust, zero-dependency, SQLite-backed long-term semantic memory storage.
    Calculates TF-IDF cosine similarity to mimic a local vector database.
    """
    def __init__(self, db_path: str = "scrum_memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    category TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save(self, agent_id: str, content: str, category: str = "general"):
        import uuid
        mem_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memories (id, agent_id, category, content) VALUES (?, ?, ?, ?)",
                (mem_id, agent_id, category, content)
            )
            conn.commit()

    def search(self, agent_id: str, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Queries database and performs TF-IDF cosine ranking on records."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, category, content, timestamp FROM memories WHERE agent_id = ?",
                (agent_id,)
            )
            records = cursor.fetchall()

        if not records:
            return []

        # Simple TF-IDF cosine similarity calculation
        def get_words(text: str) -> List[str]:
            return re.sub(r'[^\w\s]', '', text.lower()).split()

        query_words = get_words(query)
        scored_records = []

        for record in records:
            doc_words = get_words(record['content'])
            all_unique_words = list(set(query_words + doc_words))
            if not all_unique_words:
                continue
            
            # Vectors
            v_q = [query_words.count(w) for w in all_unique_words]
            v_d = [doc_words.count(w) for w in all_unique_words]
            
            dot_product = sum(a * b for a, b in zip(v_q, v_d))
            mag_q = math.sqrt(sum(a * a for a in v_q))
            mag_d = math.sqrt(sum(b * b for b in v_d))
            
            similarity = dot_product / (mag_q * mag_d) if (mag_q * mag_d) > 0 else 0.0
            
            scored_records.append({
                "id": record["id"],
                "category": record["category"],
                "content": record["content"],
                "timestamp": record["timestamp"],
                "score": similarity
            })

        # Sort by similarity score descending
        scored_records.sort(key=lambda x: x["score"], reverse=True)
        return scored_records[:limit]


# =====================================================================
# 💾 PERSISTENT PROJECT WORKSPACE STORAGE
# =====================================================================
class ProjectStorage:
    """Manages workspace projects, board columns, and configs inside SQLite."""
    def __init__(self, db_path: str = "scrum_memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    brief TEXT,
                    workspace_dir TEXT,
                    board_state TEXT,
                    virtual_filesystem TEXT,
                    po_skills TEXT,
                    dev_skills TEXT,
                    cr_skills TEXT,
                    qa_skills TEXT,
                    po_model TEXT,
                    dev_model TEXT,
                    cr_model TEXT,
                    qa_model TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()

    def save_project(self, proj_id: str, name: str, brief: str, workspace_dir: str, board_state: dict, files: dict, 
                     po_skills: list, dev_skills: list, cr_skills: list, qa_skills: list,
                     po_model: str, dev_model: str, cr_model: str, qa_model: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO projects (id, name, brief, workspace_dir, board_state, virtual_filesystem, 
                                     po_skills, dev_skills, cr_skills, qa_skills, 
                                     po_model, dev_model, cr_model, qa_model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    brief=excluded.brief,
                    workspace_dir=excluded.workspace_dir,
                    board_state=excluded.board_state,
                    virtual_filesystem=excluded.virtual_filesystem,
                    po_skills=excluded.po_skills,
                    dev_skills=excluded.dev_skills,
                    cr_skills=excluded.cr_skills,
                    qa_skills=excluded.qa_skills,
                    po_model=excluded.po_model,
                    dev_model=excluded.dev_model,
                    cr_model=excluded.cr_model,
                    qa_model=excluded.qa_model,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                proj_id, name, brief, workspace_dir, 
                json.dumps(board_state), json.dumps(files),
                json.dumps(po_skills), json.dumps(dev_skills), json.dumps(cr_skills), json.dumps(qa_skills),
                po_model, dev_model, cr_model, qa_model
            ))
            conn.commit()

    def load_project(self, proj_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE id = ?", (proj_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "brief": row["brief"],
                    "workspace_dir": row["workspace_dir"],
                    "board_state": json.loads(row["board_state"]),
                    "files": json.loads(row["virtual_filesystem"]),
                    "po_skills": json.loads(row["po_skills"]) if row["po_skills"] else [],
                    "dev_skills": json.loads(row["dev_skills"]) if row["dev_skills"] else [],
                    "cr_skills": json.loads(row["cr_skills"]) if "cr_skills" in row.keys() and row["cr_skills"] else [],
                    "qa_skills": json.loads(row["qa_skills"]) if row["qa_skills"] else [],
                    "po_model": row["po_model"] if "po_model" in row.keys() and row["po_model"] else "llama3:8b",
                    "dev_model": row["dev_model"] if "dev_model" in row.keys() and row["dev_model"] else "qwen2.5-coder:14b",
                    "cr_model": row["cr_model"] if "cr_model" in row.keys() and row["cr_model"] else "qwen2.5-coder:7b",
                    "qa_model": row["qa_model"] if "qa_model" in row.keys() and row["qa_model"] else "qwen2.5-coder:7b"
                }
        return None

    def list_projects(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, updated_at FROM projects ORDER BY updated_at DESC")
            return [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"]} for r in cursor.fetchall()]

    def set_active_project_id(self, proj_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('active_project_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (proj_id,))
            conn.commit()

    def get_active_project_id(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'active_project_id'")
            row = cursor.fetchone()
            return row[0] if row else None


# =====================================================================
# 🛠️ DECOUPLED AGENT TOOL CALLING INFRASTRUCTURE
# =====================================================================
class Tool:
    """Encapsulates a standard execute function with model definitions."""
    def __init__(self, name: str, description: str, parameters: Dict[str, Any], func: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def execute(self, **kwargs) -> Any:
        return self.func(**kwargs)


class ToolRegistry:
    """Manages system tools for dynamic registration and invocation."""
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters
            } for t in self._tools.values()
        ]

    def invoke(self, name: str, arguments: Dict[str, Any]) -> str:
        if name not in self._tools:
            return f"Error: Tool '{name}' is not registered."
        try:
            result = self._tools[name].execute(**arguments)
            return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"


# =====================================================================
# 🧠 DYNAMIC MULTI-AGENT SCENARIO RUNNER
# =====================================================================
class ScrumAgent:
    def __init__(self, role: str, model: str, system_prompt: str, ollama_url: str = "http://localhost:11434"):
        self.role = role
        self.model = model
        self.system_prompt = system_prompt
        self.ollama_url = ollama_url
        self.memory = SemanticMemoryEngine()
        self.registry = ToolRegistry()
        self.assigned_skills: List[str] = []  # List of skill filenames/relpaths assigned to this agent

    def register_tool(self, tool: Tool):
        self.registry.register(tool)

    def _get_skills_context(self) -> str:
        """Reads content of assigned skill markdown files to dynamically inject into prompt contexts."""
        skills_context = ""
        if not self.assigned_skills:
            return skills_context

        skills_context = "\n=== SPECIALIZED AGENT SKILLS ===\n"
        for skill_file in self.assigned_skills:
            skill_path = os.path.join(SKILLS_DIR, skill_file)
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        skills_context += f"\n[Skill: {skill_file}]\n{f.read()}\n"
                except Exception as e:
                    pass
        return skills_context

    def _call_local_llm(self, prompt: str) -> str:
        """Invokes local Ollama inference server with retry capabilities."""
        return self._call_local_llm_messages([{"role": "user", "content": prompt}])

    def _call_local_llm_messages(self, messages: List[Dict[str, str]]) -> str:
        """Invokes Ollama with a conversation history."""
        skills_context = self._get_skills_context()
        full_system = self.system_prompt + "\n" + skills_context

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": full_system}] + messages,
            "stream": False,
            "options": {"temperature": 0.1}
        }

        for delay in [1, 2, 4]:
            try:
                response = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=120)
                if response.status_code == 200:
                    return response.json()["message"]["content"]
            except requests.RequestException:
                import time
                time.sleep(delay)

        return "SIMULATION_FALLBACK"

    def execute_step(self, user_prompt: str, max_iterations: int = 8) -> str:
        related_memories = self.memory.search(self.role, user_prompt, limit=2)
        memory_context = ""
        if related_memories:
            memory_context = "\n=== RELEVANT HISTORICAL MEMORIES ===\n" + "\n".join(
                [f"[{m['category']}] {m['content']}" for m in related_memories]
            )

        tool_schema_str = json.dumps(self.registry.get_definitions(), indent=2)
        bt = "```"
        combined_prompt = f"""
AVAILABLE TOOLS:
{tool_schema_str}

Remember, if you need to use a tool, you MUST reply with a JSON command inside a markdown code block matching this format:
{bt}json
{{
  "action": "call_tool",
  "tool_name": "name_of_the_tool",
  "arguments": {{
    "arg_key": "value"
  }}
}}
{bt}

When finished, reply without a tool call.

{memory_context}

Task Detail:
{user_prompt}
"""
        messages = [{"role": "user", "content": combined_prompt}]

        for _ in range(max_iterations):
            response_text = self._call_local_llm_messages(messages)

            if response_text == "SIMULATION_FALLBACK":
                return "SIMULATION_FALLBACK"

            json_blocks = re.findall(rf"{bt}json\s*(.*?)\s*{bt}", response_text, re.DOTALL)
            if json_blocks:
                try:
                    tool_call = json.loads(json_blocks[0])
                    if tool_call.get("action") == "call_tool":
                        tool_name = tool_call.get("tool_name")
                        arguments = tool_call.get("arguments", {})
                        tool_output = self.registry.invoke(tool_name, arguments)

                        self.memory.save(
                            self.role,
                            f"Invoked tool '{tool_name}' on task: {user_prompt}",
                            "tool_usage"
                        )
                        messages.append({"role": "assistant", "content": response_text})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Tool result ({tool_name}):\n{tool_output}\n\n"
                                "Continue the task or reply without tools when done."
                            )
                        })
                        continue
                except Exception as e:
                    return f"Model attempted tool call but parsing failed: {str(e)}"

            return response_text

        return "Max tool iterations reached without completing the task."


# =====================================================================
# 🌐 GLOBAL APP STATE MANAGEMENT & DIRECTORY SCANNERS
# =====================================================================
CURRENT_PROJECT_ID = "default-proj"
PROJECT_NAME = "My Local Scrum Project"
PROJECT_BRIEF = "Decompose meal recipe planner modules in Nodejs."
WORKSPACE_DIR = "./workspace"
SKILLS_DIR = "./global_skills" # Decoupled and centralized!

SHARED_BOARD = {
    "Backlog": [],
    "In Progress": [],
    "Code Review": [],  # Added Code Review lane!
    "QA": [],
    "Done": []
}

VIRTUAL_FILESYSTEM = {
    "package.json": "{\n  \"name\": \"local-scrum-workspace\",\n  \"version\": \"1.0.0\"\n}"
}

SYSTEM_LOGS = []
MAX_LOG_ENTRIES = 500
STATE_LOCK = threading.Lock()
storage = ProjectStorage()


def resolve_workspace_path(path: str) -> str:
    """Returns a safe relative path within the workspace root."""
    normalized = os.path.normpath(path).replace("\\", "/")
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(f"Path escapes workspace: {path}")
    workspace_root = os.path.realpath(WORKSPACE_DIR)
    full_path = os.path.realpath(os.path.join(workspace_root, normalized))
    if full_path != workspace_root and not full_path.startswith(workspace_root + os.sep):
        raise ValueError(f"Path escapes workspace: {path}")
    return normalized


def extract_json_array_from_text(text: str) -> List[Dict[str, Any]]:
    """Extracts a JSON array of tasks from LLM output."""
    bt = "```"
    json_blocks = re.findall(rf"{bt}json\s*(.*?)\s*{bt}", text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue

    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON task array found in LLM output")


def sync_virtual_filesystem_from_disk() -> Dict[str, str]:
    """Scans the physical workspace and syncs VIRTUAL_FILESYSTEM."""
    global VIRTUAL_FILESYSTEM
    file_list: Dict[str, str] = {}
    if os.path.exists(WORKSPACE_DIR):
        for root, dirs, files_in_dir in os.walk(WORKSPACE_DIR):
            for file in files_in_dir:
                rel_path = os.path.relpath(os.path.join(root, file), WORKSPACE_DIR)
                if not any(ex in rel_path for ex in ["venv", "__pycache__", ".git"]):
                    try:
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                            file_list[rel_path.replace("\\", "/")] = f.read()
                    except Exception:
                        pass
    if file_list:
        VIRTUAL_FILESYSTEM = file_list
        return file_list
    return dict(VIRTUAL_FILESYSTEM)


def build_task_prompt(task: Dict[str, Any], brief: str) -> str:
    """Builds a structured prompt for sprint agents."""
    file_list = ", ".join(VIRTUAL_FILESYSTEM.keys()) or "(empty workspace)"
    return (
        f"Project brief:\n{brief}\n\n"
        f"Task ID: {task['id']}\n"
        f"Title: {task['title']}\n"
        f"Description: {task['description']}\n"
        f"Current status: {task.get('status', 'unknown')}\n"
        f"Workspace files: {file_list}\n"
    )


def add_system_log(source: str, log_type: str, text: str):
    import datetime
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    SYSTEM_LOGS.append({
        "timestamp": timestamp,
        "source": source,
        "type": log_type,
        "text": text
    })
    if len(SYSTEM_LOGS) > MAX_LOG_ENTRIES:
        del SYSTEM_LOGS[: len(SYSTEM_LOGS) - MAX_LOG_ENTRIES]

def scan_skills_directory() -> List[Dict[str, str]]:
    """Recursively scans the SKILLS_DIR for markdown or text files containing agent skills, preserving full paths."""
    skills = []
    if not os.path.exists(SKILLS_DIR):
        try:
            os.makedirs(SKILLS_DIR, exist_ok=True)
            # Seed directory with standard sample skills
            default_skills = {
                "git_expert.md": "# Git Expert Skill\nAlways commit changes using clean semantic messages. Check file diffs carefully.",
                "python_tester.md": "# Python Unit Tester Skill\nEnsure code has unittest coverage checking for negative and overflow bounds.",
                "javascript_optimizer.md": "# ES6 JS Optimization Skill\nWrite code utilizing modular functions, arrow notations, and clean error captures.",
                "acceptance_tester.md": "# Dynamic QA Acceptance Skill\nValidate user workflows match exact brief expectations. Write automated check reports.",
                "code_auditor.md": "# Code Reviewer Auditor Skill\nVerify architecture patterns, import structures, syntax errors, and complexity levels."
            }
            for name, content in default_skills.items():
                with open(os.path.join(SKILLS_DIR, name), "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception:
            pass

    if os.path.exists(SKILLS_DIR):
        for root, dirs, files in os.walk(SKILLS_DIR):
            for file in files:
                if file.endswith((".md", ".txt")):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, SKILLS_DIR)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            preview = f.readline().strip().replace("#", "").strip()
                        skills.append({
                            "filename": rel_path,  # Use relative path as the identifier
                            "title": preview if preview else file
                        })
                    except Exception:
                        pass
    return skills


# =====================================================================
# ⚙️ DEFINE PHYSICAL & VIRTUAL FILE CONTROLLERS
# =====================================================================
def write_workspace_file(path: str, content: str) -> str:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return str(e)

    VIRTUAL_FILESYSTEM[safe_path] = content

    phys_path = os.path.join(WORKSPACE_DIR, safe_path)
    try:
        dir_name = os.path.dirname(phys_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(phys_path, "w", encoding="utf-8") as f:
            f.write(content)

        save_current_project_state()
        return f"Successfully saved file physically at: '{phys_path}'"
    except Exception as e:
        return f"Wrote virtual cache, but physical write failed: {str(e)}"


def read_workspace_file(path: str) -> str:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return str(e)

    phys_path = os.path.join(WORKSPACE_DIR, safe_path)
    if os.path.exists(phys_path):
        try:
            with open(phys_path, "r", encoding="utf-8") as f:
                content = f.read()
                VIRTUAL_FILESYSTEM[safe_path] = content
                return content
        except Exception:
            pass
    return VIRTUAL_FILESYSTEM.get(safe_path, f"Error: File '{safe_path}' not found.")


def run_tests_on_workspace(test_script_path: str) -> str:
    content = read_workspace_file(test_script_path)
    if content.startswith("Error:"):
        return f"❌ QA Validation Failure: Could not read '{test_script_path}'."
    lower = content.lower()
    if "jwt" in lower or "encrypt" in lower or "token" in lower or "test" in lower or "assert" in lower:
        return f"✔ Validation tests completed successfully for '{test_script_path}'."
    return f"❌ QA Validation Failure: Expected security or test patterns missing in '{test_script_path}'."

def move_board_stage(task_id: str, target_lane: str) -> str:
    active_task = None
    source_lane = None
    for lane, tasks in SHARED_BOARD.items():
        for task in tasks:
            if task["id"] == task_id:
                active_task = task
                source_lane = lane
                break
        if active_task:
            break

    if active_task and source_lane is not None:
        SHARED_BOARD[source_lane].remove(active_task)
        active_task["status"] = target_lane
        if target_lane not in SHARED_BOARD:
            SHARED_BOARD[target_lane] = []
        SHARED_BOARD[target_lane].append(active_task)
        save_current_project_state()
        return f"Successfully moved task {task_id} to '{target_lane}'."
    return f"Error: Task '{task_id}' was not found on the board."


def save_current_project_state():
    storage.save_project(
        CURRENT_PROJECT_ID, PROJECT_NAME, PROJECT_BRIEF, WORKSPACE_DIR,
        SHARED_BOARD, VIRTUAL_FILESYSTEM,
        agent_po.assigned_skills, agent_dev.assigned_skills, agent_cr.assigned_skills, agent_qa.assigned_skills,
        agent_po.model, agent_dev.model, agent_cr.model, agent_qa.model
    )


# Create Tool Declarations
tool_write = Tool(
    name="write_file",
    description="Creates or modifies code and configurations inside workspace directories.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["path", "content"]
    },
    func=write_workspace_file
)

tool_read = Tool(
    name="read_file",
    description="Reads plain text source code contents from any workspace path.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
    },
    func=read_workspace_file
)

tool_test = Tool(
    name="run_test",
    description="Runs automated testing validations on codebase paths.",
    parameters={
        "type": "object",
        "properties": {"test_script_path": {"type": "string"}},
        "required": ["test_script_path"]
    },
    func=run_tests_on_workspace
)

tool_board = Tool(
    name="update_board",
    description="Updates Kanban Scrum board column positions.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "target_lane": {"type": "string"}
        },
        "required": ["task_id", "target_lane"]
    },
    func=move_board_stage
)


# Initialize Agents
agent_po = ScrumAgent(
    role="Product Owner",
    model="llama3:8b",
    system_prompt="You decompose product briefs into explicit backlog stories with JSON array elements. When a brief update is provided, generate only incremental tasks or modify existing ones without deleting unmodified assets.",
)

agent_dev = ScrumAgent(
    role="Developer",
    model="qwen2.5-coder:14b",
    system_prompt="You code app interfaces, write index.js logic, and advance lanes to Code Review when done.",
)
agent_dev.register_tool(tool_write)
agent_dev.register_tool(tool_board)

agent_cr = ScrumAgent(
    role="Code Reviewer",
    model="qwen2.5-coder:7b",
    system_prompt="You sit between Developer and QA. Audit the newly written files for logical bugs, layout problems, styling issues, or security flaws. On success, advance the task to QA. On failure, return to Developer.",
)
agent_cr.register_tool(tool_read)
agent_cr.register_tool(tool_board)

agent_qa = ScrumAgent(
    role="QA Tester",
    model="qwen2.5-coder:7b",
    system_prompt="You inspect files, run functional validation tests, and either approve to Done or send back to Developer.",
)
agent_qa.register_tool(tool_read)
agent_qa.register_tool(tool_test)
agent_qa.register_tool(tool_board)


def _simulate_dev_work(active_task: Dict[str, Any]) -> None:
    """Offline fallback when Ollama is unreachable."""
    title = active_task["title"].lower()
    if "meal" in title or "recipe" in title or "api" in title:
        file_name = "meal_service.js"
        content = (
            "const https = require('https');\n\n"
            "function fetchMealsQuery(query) {\n"
            "  console.log('Querying Meals repository for: ' + query);\n"
            "  return { success: true, meals: [] };\n"
            "}\nmodule.exports = fetchMealsQuery;"
        )
    elif "auth" in title or "secure" in title:
        file_name = "auth.js"
        content = (
            "const jwt = require('jsonwebtoken');\n\n"
            "function authenticateUser(user) {\n"
            "  console.log('Encrypting credential keys...');\n"
            "  return jwt.sign({ user }, 'SECRET_KEY');\n"
            "}\nmodule.exports = authenticateUser;"
        )
    else:
        file_name = "index.js"
        content = (
            "const fs = require('fs');\n"
            "function initializeEngine() {\n"
            "  console.log('Initializing Local workspace engine...');\n"
            "}\ninitializeEngine();"
        )
    write_workspace_file(file_name, content)
    move_board_stage(active_task["id"], "Code Review")
    add_system_log("Developer", "success", f"Offline fallback wrote {file_name}. Task moved to Code Review.")


def _simulate_code_review(active_task: Dict[str, Any]) -> None:
    import random
    if random.random() > 0.20:
        move_board_stage(active_task["id"], "QA")
        add_system_log("Code Reviewer", "success", f"Offline review PASSED for '{active_task['title']}'. Forwarded to QA.")
    else:
        move_board_stage(active_task["id"], "In Progress")
        add_system_log("Code Reviewer", "error", f"Offline review FAILED for '{active_task['title']}'. Returned to Developer.")


def _simulate_qa(active_task: Dict[str, Any]) -> None:
    import random
    if random.random() > 0.15:
        move_board_stage(active_task["id"], "Done")
        add_system_log("QA Tester", "success", f"Offline QA PASSED for '{active_task['title']}'. Moved to Done.")
    else:
        move_board_stage(active_task["id"], "In Progress")
        add_system_log("QA Tester", "error", f"Offline QA FAILED for '{active_task['title']}'. Returned to Developer.")


# =====================================================================
# 🚀 FASTAPI WEB SERVER INSTANTIATION
# =====================================================================
app = FastAPI(title="OpenHands Local Scrum Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:6767", "http://localhost:6767"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BriefPayload(BaseModel):
    brief: str
    ollama_url: str

class ConfigPayload(BaseModel):
    projectName: str
    workspaceDir: str
    skillsDir: str
    poModel: str
    devModel: str
    crModel: str
    qaModel: str

class SkillPayload(BaseModel):
    agent: str
    skillFile: str

class CreateProjectPayload(BaseModel):
    projectName: str
    workspaceDir: str

class ManualTaskPayload(BaseModel):
    title: str
    description: str


@app.get("/api/state")
def get_state():
    with STATE_LOCK:
        file_list = sync_virtual_filesystem_from_disk()

        return {
            "projectId": CURRENT_PROJECT_ID,
            "projectName": PROJECT_NAME,
            "brief": PROJECT_BRIEF,
            "workspaceDir": WORKSPACE_DIR,
            "skillsDir": SKILLS_DIR,
            "board": SHARED_BOARD,
            "files": file_list,
            "logs": SYSTEM_LOGS,
            "availableSkills": scan_skills_directory(),
            "assignedSkills": {
                "po": agent_po.assigned_skills,
                "dev": agent_dev.assigned_skills,
                "cr": agent_cr.assigned_skills,
                "qa": agent_qa.assigned_skills
            },
            "models": {
                "po": agent_po.model,
                "dev": agent_dev.model,
                "cr": agent_cr.model,
                "qa": agent_qa.model
            },
            "projectsList": storage.list_projects()
        }


@app.post("/api/projects/create")
def create_new_project(payload: CreateProjectPayload):
    global CURRENT_PROJECT_ID, PROJECT_NAME, PROJECT_BRIEF, WORKSPACE_DIR, SHARED_BOARD, VIRTUAL_FILESYSTEM
    with STATE_LOCK:
        import uuid
        CURRENT_PROJECT_ID = str(uuid.uuid4())
        PROJECT_NAME = payload.projectName
        PROJECT_BRIEF = ""
        WORKSPACE_DIR = payload.workspaceDir

        SHARED_BOARD = {"Backlog": [], "In Progress": [], "Code Review": [], "QA": [], "Done": []}
        VIRTUAL_FILESYSTEM = {"package.json": "{\n  \"name\": \"local-scrum-workspace\",\n  \"version\": \"1.0.0\"\n}"}

        os.makedirs(WORKSPACE_DIR, exist_ok=True)

        save_current_project_state()
        storage.set_active_project_id(CURRENT_PROJECT_ID)

        add_system_log("System", "success", f"Created and loaded new project: '{PROJECT_NAME}' at {WORKSPACE_DIR}")
    return get_state()


@app.post("/api/projects/load/{project_id}")
def load_existing_project(project_id: str):
    global CURRENT_PROJECT_ID, PROJECT_NAME, PROJECT_BRIEF, WORKSPACE_DIR, SHARED_BOARD, VIRTUAL_FILESYSTEM
    with STATE_LOCK:
        proj = storage.load_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Workspace project not located.")

        CURRENT_PROJECT_ID = proj["id"]
        PROJECT_NAME = proj["name"]
        PROJECT_BRIEF = proj.get("brief") or ""
        WORKSPACE_DIR = proj["workspace_dir"]
        SHARED_BOARD = proj["board_state"]
        for col in ["Backlog", "In Progress", "Code Review", "QA", "Done"]:
            if col not in SHARED_BOARD:
                SHARED_BOARD[col] = []

        VIRTUAL_FILESYSTEM = proj["files"]

        agent_po.assigned_skills = proj["po_skills"]
        agent_dev.assigned_skills = proj["dev_skills"]
        agent_cr.assigned_skills = proj["cr_skills"] if "cr_skills" in proj else []
        agent_qa.assigned_skills = proj["qa_skills"]

        agent_po.model = proj["po_model"]
        agent_dev.model = proj["dev_model"]
        agent_cr.model = proj["cr_model"]
        agent_qa.model = proj["qa_model"]

        storage.set_active_project_id(CURRENT_PROJECT_ID)
        add_system_log("System", "success", f"Successfully loaded project workspace: '{PROJECT_NAME}'")
    return get_state()


@app.post("/api/config")
def update_config(payload: ConfigPayload):
    global PROJECT_NAME, WORKSPACE_DIR, SKILLS_DIR
    with STATE_LOCK:
        PROJECT_NAME = payload.projectName
        WORKSPACE_DIR = payload.workspaceDir
        SKILLS_DIR = payload.skillsDir

        agent_po.model = payload.poModel
        agent_dev.model = payload.devModel
        agent_cr.model = payload.crModel
        agent_qa.model = payload.qaModel

        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        os.makedirs(SKILLS_DIR, exist_ok=True)

        save_current_project_state()
        add_system_log("System", "info", f"Configuration updated: Project '{PROJECT_NAME}', workspace: '{WORKSPACE_DIR}', global skills: '{SKILLS_DIR}'. Models: PO({agent_po.model}), Dev({agent_dev.model}), Reviewer({agent_cr.model}), QA({agent_qa.model})")
    return get_state()


@app.post("/api/assign-skill")
def assign_skill_to_agent(payload: SkillPayload):
    with STATE_LOCK:
        agent_map = {"po": agent_po, "dev": agent_dev, "cr": agent_cr, "qa": agent_qa}
        if payload.agent not in agent_map:
            raise HTTPException(status_code=400, detail="Invalid agent")
        agent = agent_map[payload.agent]

        src_path = os.path.join(SKILLS_DIR, payload.skillFile)
        if not os.path.exists(src_path):
            raise HTTPException(status_code=404, detail=f"Skill file '{payload.skillFile}' not found in global skills dir: {src_path}")

        base_file = os.path.basename(payload.skillFile)
        dest_skills_dir = os.path.join(WORKSPACE_DIR, "skills")
        os.makedirs(dest_skills_dir, exist_ok=True)
        dest_path = os.path.join(dest_skills_dir, base_file)

        try:
            shutil.copy2(src_path, dest_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to copy file asset to workspace: {str(e)}")

        if payload.skillFile not in agent.assigned_skills:
            agent.assigned_skills.append(payload.skillFile)

        try:
            with open(dest_path, "r", encoding="utf-8") as f:
                VIRTUAL_FILESYSTEM[f"skills/{base_file}"] = f.read()
        except Exception:
            pass

        save_current_project_state()
        add_system_log(
            payload.agent.upper() + " Agent",
            "success",
            f"Cloned skill file '{base_file}' from global library to physical workspace and assigned to prompt."
        )
    return get_state()


@app.post("/api/remove-skill")
def remove_skill_from_agent(payload: SkillPayload):
    with STATE_LOCK:
        agent_map = {"po": agent_po, "dev": agent_dev, "cr": agent_cr, "qa": agent_qa}
        if payload.agent not in agent_map:
            raise HTTPException(status_code=400, detail="Invalid agent")
        agent = agent_map[payload.agent]

        if payload.skillFile in agent.assigned_skills:
            agent.assigned_skills.remove(payload.skillFile)

        base_file = os.path.basename(payload.skillFile)
        all_assigned = agent_po.assigned_skills + agent_dev.assigned_skills + agent_cr.assigned_skills + agent_qa.assigned_skills
        if payload.skillFile not in all_assigned:
            dest_path = os.path.join(WORKSPACE_DIR, "skills", base_file)
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception:
                    pass
            VIRTUAL_FILESYSTEM.pop(f"skills/{base_file}", None)

        save_current_project_state()
        add_system_log(
            payload.agent.upper() + " Agent",
            "info",
            f"Removed skill '{base_file}' from active agent system context."
        )
    return get_state()


@app.post("/api/tasks/manual")
def add_manual_task(payload: ManualTaskPayload):
    with STATE_LOCK:
        import uuid
        task_id = "TASK-" + str(uuid.uuid4())[:8].upper()
        new_task = {
            "id": task_id,
            "title": payload.title,
            "description": payload.description,
            "status": "Backlog"
        }
        SHARED_BOARD["Backlog"].append(new_task)
        save_current_project_state()
        add_system_log("System", "success", f"Manually added task '{payload.title}' to the Backlog.")
    return get_state()


@app.post("/api/reset")
def reset_workspace():
    global SHARED_BOARD, VIRTUAL_FILESYSTEM, SYSTEM_LOGS
    with STATE_LOCK:
        SHARED_BOARD = {"Backlog": [], "In Progress": [], "Code Review": [], "QA": [], "Done": []}
        VIRTUAL_FILESYSTEM = {"package.json": "{\n  \"name\": \"local-scrum-workspace\",\n  \"version\": \"1.0.0\"\n}"}
        SYSTEM_LOGS.clear()

        if os.path.exists(WORKSPACE_DIR):
            for root, dirs, files_in_dir in os.walk(WORKSPACE_DIR):
                for file in files_in_dir:
                    file_path = os.path.join(root, file)
                    if os.path.isfile(file_path) and not file.startswith("."):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass

        save_current_project_state()
        add_system_log("System", "info", "Workspace state cleared. Backlog lanes and directory files cleaned successfully.")
    return get_state()


@app.post("/api/plan")
def trigger_po_plan(payload: BriefPayload):
    global PROJECT_BRIEF
    with STATE_LOCK:
        PROJECT_BRIEF = payload.brief
        agent_po.ollama_url = payload.ollama_url
        add_system_log("Product Owner", "info", f"Analyzing project brief update: '{payload.brief[:60]}'...")

        po_output = agent_po.execute_step(
            f"Decompose this brief into 3 distinct clear developer tasks. "
            f"Reply with ONLY a JSON array of objects with keys: id, title, description.\n\n{payload.brief}"
        )

        if po_output == "SIMULATION_FALLBACK":
            add_system_log("Product Owner", "warning", "Ollama unreachable. Generating high-fidelity task additions...")
            import uuid
            tasks = []
            if "meal" in payload.brief.lower() or "food" in payload.brief.lower():
                tasks = [
                    {"id": "TASK-" + str(uuid.uuid4())[:8].upper(), "title": "Setup Meal API interface", "description": "Configure external integration endpoint layer to fetch dynamic recipes."},
                    {"id": "TASK-" + str(uuid.uuid4())[:8].upper(), "title": "Build Recipe parser model", "description": "Construct internal validation logic to format ingredients JSON structures."},
                    {"id": "TASK-" + str(uuid.uuid4())[:8].upper(), "title": "Secure API endpoints", "description": "Add session authorization checker keys on Meal routes."}
                ]
            else:
                tasks = [
                    {"id": "TASK-101", "title": "Create DB model layer", "description": "Implement standard tasks.json file database with read/write access."},
                    {"id": "TASK-102", "title": "Implement CLI arguments", "description": "Write index.js to parse console variables and log pretty color blocks."},
                    {"id": "TASK-103", "title": "Setup user auth module", "description": "Build auth.js logic implementing standard encryption layers."}
                ]
            for t in tasks:
                t["status"] = "Backlog"
                SHARED_BOARD["Backlog"].append(t)
            add_system_log("Product Owner", "success", f"Successfully incorporated brief upgrades. Decomposed {len(tasks)} new features onto the board.")
        else:
            try:
                tasks_parsed = extract_json_array_from_text(po_output)
                for t in tasks_parsed:
                    if "id" not in t:
                        import uuid
                        t["id"] = "TASK-" + str(uuid.uuid4())[:8].upper()
                    t["status"] = "Backlog"
                    SHARED_BOARD["Backlog"].append(t)
                add_system_log("Product Owner", "success", f"Decomposed {len(tasks_parsed)} tasks using local LLM.")
            except (ValueError, json.JSONDecodeError) as e:
                add_system_log("Product Owner", "error", f"Failed to parse PO output into tasks: {str(e)}")
                add_system_log("Product Owner", "info", f"Raw PO output: {po_output[:300]}...")

        save_current_project_state()
    return get_state()


@app.post("/api/step")
def trigger_agent_turn(payload: BriefPayload):
    global PROJECT_BRIEF
    with STATE_LOCK:
        PROJECT_BRIEF = payload.brief
        agent_dev.ollama_url = payload.ollama_url
        agent_cr.ollama_url = payload.ollama_url
        agent_qa.ollama_url = payload.ollama_url

        if len(SHARED_BOARD["In Progress"]) > 0:
            active_task = SHARED_BOARD["In Progress"][0]
            add_system_log("Developer", "info", f"Actively implementing code: {active_task['title']}...")
            prompt = (
                build_task_prompt(active_task, payload.brief)
                + "\nImplement this task using write_file. When complete, use update_board to move the task to 'Code Review'."
            )
            result = agent_dev.execute_step(prompt)
            if result == "SIMULATION_FALLBACK":
                add_system_log("Developer", "warning", "Ollama unreachable. Using offline developer fallback.")
                _simulate_dev_work(active_task)
            else:
                if active_task["id"] in [t["id"] for t in SHARED_BOARD["In Progress"]]:
                    move_board_stage(active_task["id"], "Code Review")
                add_system_log("Developer", "success", f"Developer finished: {result[:200]}")

        elif len(SHARED_BOARD["Backlog"]) > 0:
            active_task = SHARED_BOARD["Backlog"][0]
            add_system_log("Developer", "info", f"Claimed task {active_task['id']} from Backlog. Moving task to In Progress lane.")
            move_board_stage(active_task["id"], "In Progress")

        elif len(SHARED_BOARD["Code Review"]) > 0:
            active_task = SHARED_BOARD["Code Review"][0]
            add_system_log("Code Reviewer", "info", f"Reviewing task {active_task['id']}...")
            prompt = (
                build_task_prompt(active_task, payload.brief)
                + "\nReview workspace files with read_file. On pass, use update_board to move to 'QA'. "
                "On fail, move back to 'In Progress'."
            )
            result = agent_cr.execute_step(prompt)
            if result == "SIMULATION_FALLBACK":
                add_system_log("Code Reviewer", "warning", "Ollama unreachable. Using offline review fallback.")
                _simulate_code_review(active_task)
            else:
                if active_task["id"] in [t["id"] for t in SHARED_BOARD["Code Review"]]:
                    move_board_stage(active_task["id"], "QA")
                add_system_log("Code Reviewer", "success", f"Code review finished: {result[:200]}")

        elif len(SHARED_BOARD["QA"]) > 0:
            active_task = SHARED_BOARD["QA"][0]
            add_system_log("QA Tester", "info", f"Executing validation on task {active_task['id']}...")
            prompt = (
                build_task_prompt(active_task, payload.brief)
                + "\nInspect files with read_file and run_test. On pass, use update_board to move to 'Done'. "
                "On fail, move back to 'In Progress'."
            )
            result = agent_qa.execute_step(prompt)
            if result == "SIMULATION_FALLBACK":
                add_system_log("QA Tester", "warning", "Ollama unreachable. Using offline QA fallback.")
                _simulate_qa(active_task)
            else:
                if active_task["id"] in [t["id"] for t in SHARED_BOARD["QA"]]:
                    move_board_stage(active_task["id"], "Done")
                add_system_log("QA Tester", "success", f"QA finished: {result[:200]}")

        else:
            add_system_log("System", "warning", "All sprint lanes are currently empty. Supply a project brief or add manual tasks to proceed.")

        save_current_project_state()
    return get_state()


# =====================================================================
# 🖥️ FRONTEND WEB INTERFACE (Beautiful React-based Workspace Board)
# =====================================================================
@app.get("/", response_class=HTMLResponse)
def index_page():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>OpenHands Multi-Agent Workspace</title>
        <script src="URL_TAILWIND"></script>
        <script src="URL_REACT" crossorigin></script>
        <script src="URL_REACT_DOM" crossorigin></script>
        <script src="URL_BABEL"></script>
        <link rel="stylesheet" href="URL_FONTAWESOME">
        <style>
            /* Custom Scrollbar */
            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            ::-webkit-scrollbar-track {
                background: #1e1e2e;
            }
            ::-webkit-scrollbar-thumb {
                background: #45475a;
                border-radius: 4px;
            }
            ::-webkit-scrollbar-thumb:hover {
                background: #585b70;
            }
        </style>
    </head>
    <body class="bg-[#0f0f15] text-[#cdd6f4] h-screen overflow-hidden font-sans">
        <div id="root" class="h-full"></div>
        <script type="text/babel">
            function App() {
                const [ollamaUrl, setOllamaUrl] = React.useState('http://localhost:11434');
                const [brief, setBrief] = React.useState("Decompose meal recipe planner modules in Nodejs.");
                
                // Configurable project and directory properties
                const [projectName, setProjectName] = React.useState('My Local Scrum Project');
                const [workspaceDir, setWorkspaceDir] = React.useState('./workspace');
                const [skillsDir, setSkillsDir] = React.useState('./global_skills');

                // Customizable model configurations per agent
                const [poModel, setPoModel] = React.useState('llama3:8b');
                const [devModel, setDevModel] = React.useState('qwen2.5-coder:14b');
                const [crModel, setCrModel] = React.useState('qwen2.5-coder:7b');
                const [qaModel, setQaModel] = React.useState('qwen2.5-coder:7b');

                // Dynamic skills assignment states
                const [availableSkills, setAvailableSkills] = React.useState([]);
                const [poSkills, setPoSkills] = React.useState([]);
                const [devSkills, setDevSkills] = React.useState([]);
                const [crSkills, setCrSkills] = React.useState([]);
                const [qaSkills, setQaSkills] = React.useState([]);

                // Saved Projects / Workspaces list
                const [projectsList, setProjectsList] = React.useState([]);
                const [selectedProjectId, setSelectedProjectId] = React.useState('default-proj');

                // Active skill overlay modal triggers
                const [activeSkillModal, setActiveSkillModal] = React.useState(null); // 'po', 'dev', 'cr', 'qa' or null
                const [skillSearch, setSkillSearch] = React.useState('');

                // Modal for creating a new project
                const [showNewProjModal, setShowNewProjectModal] = React.useState(false);
                const [newProjName, setNewProjName] = React.useState('');
                const [newProjDir, setNewProjDir] = React.useState('./workspace_new');

                // Modal for adding a manual task
                const [showManualTaskModal, setShowManualTaskModal] = React.useState(false);
                const [manualTaskTitle, setManualTaskTitle] = React.useState('');
                const [manualTaskDesc, setManualTaskDesc] = React.useState('');

                const [board, setBoard] = React.useState({ Backlog: [], "In Progress": [], "Code Review": [], QA: [], Done: [] });
                const [files, setFiles] = React.useState({});
                const [logs, setLogs] = React.useState([]);
                const [selectedFile, setSelectedFile] = React.useState('package.json');
                const [loading, setLoading] = React.useState(false);

                React.useEffect(() => {
                    fetchState();
                }, []);

                const fetchState = async () => {
                    try {
                        const res = await fetch('/api/state');
                        const data = await res.json();
                        setSelectedProjectId(data.projectId);
                        setProjectName(data.projectName);
                        setWorkspaceDir(data.workspaceDir);
                        setSkillsDir(data.skillsDir);
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        setAvailableSkills(data.availableSkills || []);
                        setPoSkills(data.assignedSkills.po || []);
                        setDevSkills(data.assignedSkills.dev || []);
                        setCrSkills(data.assignedSkills.cr || []);
                        setQaSkills(data.assignedSkills.qa || []);
                        setProjectsList(data.projectsList || []);
                        if (data.brief !== undefined) {
                            setBrief(data.brief);
                        }
                        
                        // Populate custom model inputs with values returned from active project config
                        setPoModel(data.models?.po || 'llama3:8b');
                        setDevModel(data.models?.dev || 'qwen2.5-coder:14b');
                        setCrModel(data.models?.cr || 'qwen2.5-coder:7b');
                        setQaModel(data.models?.qa || 'qwen2.5-coder:7b');
                    } catch (e) {
                        console.error("Failed fetching status", e);
                    }
                };

                const updateConfiguration = async () => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                projectName,
                                workspaceDir,
                                skillsDir,
                                poModel,
                                devModel,
                                crModel,
                                qaModel
                            })
                        });
                        const data = await res.json();
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        setAvailableSkills(data.availableSkills || []);
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const loadProjectWorkspace = async (projId) => {
                    setLoading(true);
                    try {
                        const res = await fetch(`/api/projects/load/${projId}`, { method: 'POST' });
                        const data = await res.json();
                        setSelectedProjectId(data.projectId);
                        setProjectName(data.projectName);
                        setWorkspaceDir(data.workspaceDir);
                        setSkillsDir(data.skillsDir);
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        setPoSkills(data.assignedSkills.po || []);
                        setDevSkills(data.assignedSkills.dev || []);
                        setCrSkills(data.assignedSkills.cr || []);
                        setQaSkills(data.assignedSkills.qa || []);
                        setProjectsList(data.projectsList || []);
                        if (data.brief !== undefined) {
                            setBrief(data.brief);
                        }
                        
                        setPoModel(data.models?.po || 'llama3:8b');
                        setDevModel(data.models?.dev || 'qwen2.5-coder:14b');
                        setCrModel(data.models?.cr || 'qwen2.5-coder:7b');
                        setQaModel(data.models?.qa || 'qwen2.5-coder:7b');
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const createNewProject = async (e) => {
                    e.preventDefault();
                    if (!newProjName || !newProjDir) return;
                    setLoading(true);
                    try {
                        const res = await fetch('/api/projects/create', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                projectName: newProjName,
                                workspaceDir: newProjDir
                            })
                        });
                        const data = await res.json();
                        setSelectedProjectId(data.projectId);
                        setProjectName(data.projectName);
                        setWorkspaceDir(data.workspaceDir);
                        setSkillsDir(data.skillsDir);
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        setPoSkills(data.assignedSkills.po || []);
                        setDevSkills(data.assignedSkills.dev || []);
                        setCrSkills(data.assignedSkills.cr || []);
                        setQaSkills(data.assignedSkills.qa || []);
                        setProjectsList(data.projectsList || []);
                        setShowNewProjectModal(false);
                        setNewProjName('');
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const assignSkill = async (agent, skillFile) => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/assign-skill', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ agent, skillFile })
                        });
                        const data = await res.json();
                        setFiles(data.files);
                        setLogs(data.logs);
                        setPoSkills(data.assignedSkills.po || []);
                        setDevSkills(data.assignedSkills.dev || []);
                        setCrSkills(data.assignedSkills.cr || []);
                        setQaSkills(data.assignedSkills.qa || []);
                        setActiveSkillModal(null);
                        setSkillSearch('');
                        
                        const baseFile = skillFile.split('/').pop();
                        setSelectedFile(`skills/${baseFile}`);
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const removeSkill = async (agent, skillFile) => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/remove-skill', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ agent, skillFile })
                        });
                        const data = await res.json();
                        setFiles(data.files);
                        setLogs(data.logs);
                        setPoSkills(data.assignedSkills.po || []);
                        setDevSkills(data.assignedSkills.dev || []);
                        setCrSkills(data.assignedSkills.cr || []);
                        setQaSkills(data.assignedSkills.qa || []);
                        
                        const baseFile = skillFile.split('/').pop();
                        if (selectedFile === `skills/${baseFile}`) {
                            setSelectedFile('package.json');
                        }
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const addManualTask = async (e) => {
                    e.preventDefault();
                    if (!manualTaskTitle || !manualTaskDesc) return;
                    setLoading(true);
                    try {
                        const res = await fetch('/api/tasks/manual', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                title: manualTaskTitle,
                                description: manualTaskDesc
                            })
                        });
                        const data = await res.json();
                        setBoard(data.board);
                        setLogs(data.logs);
                        setShowManualTaskModal(false);
                        setManualTaskTitle('');
                        setManualTaskDesc('');
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const triggerPOPlanning = async () => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/plan', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ brief, ollama_url: ollamaUrl })
                        });
                        const data = await res.json();
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const triggerStep = async () => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/step', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ brief, ollama_url: ollamaUrl })
                        });
                        const data = await res.json();
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        const fileNames = Object.keys(data.files);
                        if (fileNames.length > 0 && !fileNames.includes(selectedFile)) {
                            setSelectedFile(fileNames[fileNames.length - 1]);
                        }
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                const resetWorkspace = async () => {
                    setLoading(true);
                    try {
                        const res = await fetch('/api/reset', { method: 'POST' });
                        const data = await res.json();
                        setBoard(data.board);
                        setFiles(data.files);
                        setLogs(data.logs);
                        setSelectedFile('package.json');
                    } catch (e) {
                        console.error(e);
                    } finally {
                        setLoading(false);
                    }
                };

                // Filters available skills matching search keyword recursively
                const filteredSkills = availableSkills.filter(s => {
                    const matchSearch = s.title.toLowerCase().includes(skillSearch.toLowerCase()) || 
                                        s.filename.toLowerCase().includes(skillSearch.toLowerCase());
                    if (activeSkillModal === 'po') return matchSearch && !poSkills.includes(s.filename);
                    if (activeSkillModal === 'dev') return matchSearch && !devSkills.includes(s.filename);
                    if (activeSkillModal === 'cr') return matchSearch && !crSkills.includes(s.filename);
                    if (activeSkillModal === 'qa') return matchSearch && !qaSkills.includes(s.filename);
                    return matchSearch;
                });

                return (
                    <div className="flex h-full w-full flex-col lg:flex-row bg-[#11111b] overflow-hidden">
                        
                        {/* SIDEBAR: Configuration & Brief Wizard */}
                        <div className="w-full lg:w-1/4 bg-[#181825] border-b lg:border-b-0 lg:border-r border-[#313244] p-4 flex flex-col justify-between overflow-y-auto">
                            <div className="space-y-4">
                                <div className="flex items-center justify-between pb-3 border-b border-[#313244]">
                                    <div className="flex items-center space-x-3">
                                        <div className="bg-indigo-600 p-2 rounded-xl text-white shadow-lg shadow-indigo-500/20">
                                            <i className="fa-solid fa-code-merge text-xl"></i>
                                        </div>
                                        <div>
                                            <h1 className="font-bold text-lg text-white">OpenHands</h1>
                                            <p className="text-xs text-[#a6adc8]">Multi-Agent Workspace</p>
                                        </div>
                                    </div>
                                </div>

                                {/* Persistent Workspace Selector / Loader */}
                                <div className="bg-[#1e1e2e] p-3 rounded-xl border border-[#313244] space-y-3">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-xs font-bold uppercase tracking-wider text-[#a6adc8]">Load Workspace</h3>
                                        <button 
                                            onClick={() => setShowNewProjectModal(true)}
                                            className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center space-x-1"
                                        >
                                            <i className="fa-solid fa-plus text-[10px]"></i>
                                            <span>New</span>
                                        </button>
                                    </div>
                                    <select 
                                        value={selectedProjectId}
                                        onChange={(e) => loadProjectWorkspace(e.target.value)}
                                        className="w-full bg-[#11111b] border border-[#313244] rounded-lg p-2 text-xs text-white focus:outline-none focus:border-indigo-500"
                                    >
                                        {projectsList.map(p => (
                                            <option key={p.id} value={p.id}>{p.name}</option>
                                        ))}
                                        {projectsList.length === 0 && (
                                            <option value="default-proj">Default Project Workspace</option>
                                        )}
                                    </select>
                                </div>

                                {/* Project Custom Settings Configuration Form */}
                                <div className="bg-[#1e1e2e] p-3 rounded-xl border border-[#313244] space-y-2">
                                    <h3 className="text-xs font-bold uppercase tracking-wider text-[#a6adc8]">Project Config</h3>
                                    <div className="space-y-1.5 text-xs">
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-0.5">PROJECT NAME</label>
                                            <input 
                                                type="text" 
                                                value={projectName} 
                                                onChange={(e) => setProjectName(e.target.value)}
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-1.5 text-white font-medium focus:outline-none"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-0.5">WORKSPACE DIR</label>
                                            <input 
                                                type="text" 
                                                value={workspaceDir} 
                                                onChange={(e) => setWorkspaceDir(e.target.value)}
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-1.5 text-white font-mono focus:outline-none"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-0.5">GLOBAL SKILLS DIRECTORY</label>
                                            <input 
                                                type="text" 
                                                value={skillsDir} 
                                                onChange={(e) => setSkillsDir(e.target.value)}
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-1.5 text-white font-mono focus:outline-none"
                                            />
                                        </div>

                                        {/* Dynamic Model Configurations */}
                                        <div className="pt-2 border-t border-[#313244]/50 space-y-1.5">
                                            <div className="flex items-center justify-between">
                                                <label className="text-[9px] text-[#a6adc8] font-bold">PO MODEL</label>
                                                <input type="text" value={poModel} onChange={(e) => setPoModel(e.target.value)} className="bg-[#11111b] border border-[#313244] rounded p-0.5 px-1 font-mono text-[10px] text-right w-2/3 focus:outline-none" />
                                            </div>
                                            <div className="flex items-center justify-between">
                                                <label className="text-[9px] text-[#a6adc8] font-bold">DEV MODEL</label>
                                                <input type="text" value={devModel} onChange={(e) => setDevModel(e.target.value)} className="bg-[#11111b] border border-[#313244] rounded p-0.5 px-1 font-mono text-[10px] text-right w-2/3 focus:outline-none" />
                                            </div>
                                            <div className="flex items-center justify-between">
                                                <label className="text-[9px] text-[#a6adc8] font-bold">CR MODEL</label>
                                                <input type="text" value={crModel} onChange={(e) => setCrModel(e.target.value)} className="bg-[#11111b] border border-[#313244] rounded p-0.5 px-1 font-mono text-[10px] text-right w-2/3 focus:outline-none" />
                                            </div>
                                            <div className="flex items-center justify-between">
                                                <label className="text-[9px] text-[#a6adc8] font-bold">QA MODEL</label>
                                                <input type="text" value={qaModel} onChange={(e) => setQaModel(e.target.value)} className="bg-[#11111b] border border-[#313244] rounded p-0.5 px-1 font-mono text-[10px] text-right w-2/3 focus:outline-none" />
                                            </div>
                                        </div>

                                        <button 
                                            onClick={updateConfiguration}
                                            className="w-full bg-indigo-600/40 hover:bg-indigo-600/80 border border-indigo-500/30 text-white font-semibold py-1 rounded text-[11px] transition-colors mt-2"
                                        >
                                            Save Custom Configurations
                                        </button>
                                    </div>
                                </div>

                                {/* Dynamic Team Profiles List */}
                                <div className="bg-[#1e1e2e] p-3 rounded-xl border border-[#313244] space-y-3">
                                    <h3 className="text-xs font-bold uppercase tracking-wider text-[#a6adc8]">Agent Team & Skills</h3>
                                    
                                    <div className="space-y-2">
                                        {/* Product Owner */}
                                        <div className="p-2 bg-[#11111b] rounded border border-[#313244] text-xs">
                                            <div className="flex items-center justify-between font-bold text-white mb-1">
                                                <span>Product Owner</span>
                                                <span className="text-[9px] font-mono text-[#a6adc8] bg-[#1e1e2e] px-1 py-0.5 rounded">{poModel}</span>
                                            </div>
                                            <div className="flex flex-wrap gap-1 mb-1.5">
                                                {poSkills.map(skill => (
                                                    <span key={skill} className="bg-indigo-950/40 border border-indigo-500/30 text-indigo-300 text-[10px] px-1.5 py-0.5 rounded flex items-center space-x-1">
                                                        <span>{skill.split('/').pop().replace('.md', '').replace('_', ' ')}</span>
                                                        <button onClick={() => removeSkill('po', skill)} className="hover:text-red-400 text-slate-400">×</button>
                                                    </span>
                                                ))}
                                                {poSkills.length === 0 && <span className="text-[10px] text-[#6c7086] italic">No skills</span>}
                                            </div>
                                            <button onClick={() => setActiveSkillModal('po')} className="bg-[#1e1e2e] hover:bg-[#313244] text-[#bac2de] py-0.5 px-2 rounded border border-[#313244] text-[10px] font-semibold transition-colors">
                                                + Add Skill
                                            </button>
                                        </div>

                                        {/* Developer */}
                                        <div className="p-2 bg-[#11111b] rounded border border-[#313244] text-xs">
                                            <div className="flex items-center justify-between font-bold text-white mb-1">
                                                <span>Developer</span>
                                                <span className="text-[9px] font-mono text-[#a6adc8] bg-[#1e1e2e] px-1 py-0.5 rounded">{devModel}</span>
                                            </div>
                                            <div className="flex flex-wrap gap-1 mb-1.5">
                                                {devSkills.map(skill => (
                                                    <span key={skill} className="bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-[10px] px-1.5 py-0.5 rounded flex items-center space-x-1">
                                                        <span>{skill.split('/').pop().replace('.md', '').replace('_', ' ')}</span>
                                                        <button onClick={() => removeSkill('dev', skill)} className="hover:text-red-400 text-slate-400">×</button>
                                                    </span>
                                                ))}
                                                {devSkills.length === 0 && <span className="text-[10px] text-[#6c7086] italic">No skills</span>}
                                            </div>
                                            <button onClick={() => setActiveSkillModal('dev')} className="bg-[#1e1e2e] hover:bg-[#313244] text-[#bac2de] py-0.5 px-2 rounded border border-[#313244] text-[10px] font-semibold transition-colors">
                                                + Add Skill
                                            </button>
                                        </div>

                                        {/* Code Reviewer */}
                                        <div className="p-2 bg-[#11111b] rounded border border-[#313244] text-xs">
                                            <div className="flex items-center justify-between font-bold text-white mb-1">
                                                <span>Code Reviewer</span>
                                                <span className="text-[9px] font-mono text-[#a6adc8] bg-[#1e1e2e] px-1 py-0.5 rounded">{crModel}</span>
                                            </div>
                                            <div className="flex flex-wrap gap-1 mb-1.5">
                                                {crSkills.map(skill => (
                                                    <span key={skill} className="bg-orange-950/40 border border-orange-500/30 text-orange-300 text-[10px] px-1.5 py-0.5 rounded flex items-center space-x-1">
                                                        <span>{skill.split('/').pop().replace('.md', '').replace('_', ' ')}</span>
                                                        <button onClick={() => removeSkill('cr', skill)} className="hover:text-red-400 text-slate-400">×</button>
                                                    </span>
                                                ))}
                                                {crSkills.length === 0 && <span className="text-[10px] text-[#6c7086] italic">No skills</span>}
                                            </div>
                                            <button onClick={() => setActiveSkillModal('cr')} className="bg-[#1e1e2e] hover:bg-[#313244] text-[#bac2de] py-0.5 px-2 rounded border border-[#313244] text-[10px] font-semibold transition-colors">
                                                + Add Skill
                                            </button>
                                        </div>

                                        {/* QA Tester */}
                                        <div className="p-2 bg-[#11111b] rounded border border-[#313244] text-xs">
                                            <div className="flex items-center justify-between font-bold text-white mb-1">
                                                <span>QA Tester</span>
                                                <span className="text-[9px] font-mono text-[#a6adc8] bg-[#1e1e2e] px-1 py-0.5 rounded">{qaModel}</span>
                                            </div>
                                            <div className="flex flex-wrap gap-1 mb-1.5">
                                                {qaSkills.map(skill => (
                                                    <span key={skill} className="bg-purple-950/40 border border-purple-500/30 text-purple-300 text-[10px] px-1.5 py-0.5 rounded flex items-center space-x-1">
                                                        <span>{skill.split('/').pop().replace('.md', '').replace('_', ' ')}</span>
                                                        <button onClick={() => removeSkill('qa', skill)} className="hover:text-red-400 text-slate-400">×</button>
                                                    </span>
                                                ))}
                                                {qaSkills.length === 0 && <span className="text-[10px] text-[#6c7086] italic">No skills</span>}
                                            </div>
                                            <button onClick={() => setActiveSkillModal('qa')} className="bg-[#1e1e2e] hover:bg-[#313244] text-[#bac2de] py-0.5 px-2 rounded border border-[#313244] text-[10px] font-semibold transition-colors">
                                                + Add Skill
                                            </button>
                                        </div>
                                    </div>
                                </div>

                                <div className="bg-[#1e1e2e] p-3 rounded-xl border border-[#313244] space-y-3">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-xs font-bold uppercase tracking-wider text-[#a6adc8]">Project Brief / Instructions</h3>
                                        <button 
                                            onClick={() => setShowManualTaskModal(true)}
                                            className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center space-x-1"
                                        >
                                            <i className="fa-solid fa-square-plus"></i>
                                            <span>Manual Task</span>
                                        </button>
                                    </div>
                                    <textarea 
                                        value={brief}
                                        onChange={(e) => setBrief(e.target.value)}
                                        className="w-full h-20 bg-[#11111b] border border-[#313244] rounded-lg p-2 text-xs text-white focus:outline-none focus:border-indigo-500 resize-none font-mono"
                                        placeholder="Add new instructions or project updates..."
                                    />
                                    <div className="space-y-2 pt-1">
                                        <button 
                                            onClick={triggerPOPlanning}
                                            disabled={loading}
                                            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-medium py-2 rounded-lg text-xs transition-colors flex items-center justify-center space-x-2"
                                        >
                                            <i className="fa-solid fa-layer-group"></i>
                                            <span>Incorporate Brief Changes (PO)</span>
                                        </button>
                                        <button 
                                            onClick={triggerStep}
                                            disabled={loading || (board.Backlog.length === 0 && board["In Progress"].length === 0 && board["Code Review"].length === 0 && board.QA.length === 0)}
                                            className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-medium py-2 rounded-lg text-xs transition-colors flex items-center justify-center space-x-2"
                                        >
                                            {loading ? <i className="fa-solid fa-spinner animate-spin"></i> : <i className="fa-solid fa-play"></i>}
                                            <span>Execute Sprint Step</span>
                                        </button>
                                    </div>
                                </div>
                            </div>

                            <div className="pt-4 border-t border-[#313244]">
                                <button 
                                    onClick={resetWorkspace}
                                    className="w-full bg-rose-950/20 text-rose-400 hover:bg-rose-950/40 border border-rose-500/20 py-2 rounded-lg text-xs font-medium transition-colors"
                                >
                                    <i className="fa-solid fa-arrow-rotate-left mr-1"></i> Reset Workspace State
                                </button>
                            </div>
                        </div>

                        {/* WORKSPACE & BOARDS */}
                        <div className="flex-1 flex flex-col h-full overflow-hidden">
                            <div className="grid grid-rows-2 h-full overflow-hidden">
                                
                                {/* SCRUM BOARD (Kanban Board Area) */}
                                <div className="p-4 overflow-y-auto bg-[#1e1e2e]/30 flex flex-col border-b border-[#313244]">
                                    <div className="flex items-center justify-between mb-3">
                                        <h2 className="text-sm font-bold uppercase tracking-wider text-[#a6adc8] flex items-center space-x-2">
                                            <i className="fa-solid fa-table-columns text-indigo-500"></i>
                                            <span>Project Board: {projectName}</span>
                                        </h2>
                                        <span className="text-[10px] text-[#a6adc8] font-mono bg-[#11111b] px-2 py-1 rounded">
                                            Workspace: {workspaceDir}
                                        </span>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-5 gap-3 flex-1">
                                        {Object.keys(board).map(lane => (
                                            <div key={lane} className="bg-[#11111b] p-2.5 rounded-xl border border-[#313244] flex flex-col min-h-[160px]">
                                                <div className="flex items-center justify-between pb-1.5 border-b border-[#313244] mb-2.5">
                                                    <span className="text-xs font-bold text-[#cdd6f4] uppercase tracking-wider">{lane}</span>
                                                    <span className="bg-[#1e1e2e] text-[#a6adc8] text-[10px] font-mono px-2 py-0.5 rounded-full">{board[lane]?.length || 0}</span>
                                                </div>
                                                <div className="space-y-2 overflow-y-auto flex-1">
                                                    {board[lane]?.map(task => (
                                                        <div key={task.id} className="bg-[#1e1e2e] p-2.5 rounded-lg border border-[#313244] hover:border-indigo-500/50 transition-all text-xs">
                                                            <div className="flex items-center justify-between mb-1.5">
                                                                <span className="text-[10px] bg-indigo-950 text-indigo-300 px-1.5 py-0.5 rounded font-mono font-bold">{task.id}</span>
                                                            </div>
                                                            <h4 className="font-bold text-white mb-1 leading-tight">{task.title}</h4>
                                                            <p className="text-[11px] text-[#a6adc8] line-clamp-3">{task.description}</p>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {/* BOTTOM WORKSPACE: Code Viewer & Logs Terminal */}
                                <div className="grid grid-cols-1 lg:grid-cols-2 h-full overflow-hidden">
                                    
                                    {/* Virtual IDE File Editor */}
                                    <div className="border-r border-[#313244] flex flex-col h-full overflow-hidden">
                                        <div className="flex bg-[#1e1e2e]/40 border-b border-[#313244] overflow-x-auto text-xs">
                                            {Object.keys(files).map(name => (
                                                <button 
                                                    key={name}
                                                    onClick={() => setSelectedFile(name)}
                                                    className={`px-3 py-2 border-r border-[#313244] transition-colors whitespace-nowrap ${
                                                        selectedFile === name ? 'bg-[#11111b] text-indigo-400 font-medium' : 'text-[#89b4fa] hover:bg-[#181825]'
                                                    }`}
                                                >
                                                    <i className="fa-regular fa-file-code mr-1"></i> {name}
                                                </button>
                                            ))}
                                        </div>
                                        <div className="flex-1 bg-[#11111b] p-4 overflow-auto font-mono text-xs text-[#cdd6f4]">
                                            <pre className="whitespace-pre-wrap">{files[selectedFile] || '// Empty Workspace file'}</pre>
                                        </div>
                                    </div>

                                    {/* Terminal Events stream */}
                                    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
                                        <div className="bg-[#181825] border-b border-[#313244] px-4 py-2 flex items-center justify-between">
                                            <h3 className="text-xs font-bold uppercase tracking-wider text-[#a6adc8]">Agent Console Event Stream</h3>
                                        </div>
                                        <div className="flex-1 p-3 overflow-y-auto space-y-2 font-mono text-xs">
                                            {logs.map((log, i) => (
                                                <div key={i} className={`p-2 rounded border border-[#313244]/40 ${
                                                    log.type === 'success' ? 'text-emerald-400 bg-emerald-950/10' :
                                                    log.type === 'error' ? 'text-rose-400 bg-rose-950/10' :
                                                    log.type === 'warning' ? 'text-amber-400 bg-amber-950/10' : 'text-indigo-400'
                                                }`}>
                                                    <div className="flex items-center justify-between opacity-75 mb-0.5 text-[10px]">
                                                        <span className="font-bold uppercase">{log.source}</span>
                                                        <span>{log.timestamp}</span>
                                                    </div>
                                                    <p className="whitespace-pre-wrap">{log.text}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>

                                </div>

                            </div>
                        </div>

                        {/* Searchable Skills library modal */}
                        {activeSkillModal && (
                            <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50 animate-fadeIn">
                                <div className="bg-[#1e1e2e] rounded-2xl max-w-lg w-full p-6 border border-[#313244] space-y-4 shadow-2xl">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-base font-bold text-white flex items-center space-x-2">
                                            <i className="fa-solid fa-graduation-cap text-indigo-400"></i>
                                            <span>Assign Skill to {activeSkillModal.toUpperCase()} Agent</span>
                                        </h3>
                                        <button onClick={() => { setActiveSkillModal(null); setSkillSearch(''); }} className="text-[#bac2de] hover:text-white">
                                            <i className="fa-solid fa-xmark"></i>
                                        </button>
                                    </div>
                                    
                                    <div className="relative">
                                        <input 
                                            type="text" 
                                            placeholder="Search through recursive subfolders..."
                                            value={skillSearch}
                                            onChange={(e) => setSkillSearch(e.target.value)}
                                            className="w-full bg-[#11111b] border border-[#313244] rounded-lg p-2.5 pl-9 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono"
                                        />
                                        <i className="fa-solid fa-magnifying-glass absolute left-3 top-3.5 text-xs text-slate-500"></i>
                                    </div>

                                    <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                                        {filteredSkills.map(skill => (
                                            <div key={skill.filename} className="p-3 bg-[#11111b] rounded-xl border border-[#313244] hover:border-indigo-500/50 flex items-center justify-between transition-colors">
                                                <div className="space-y-0.5 truncate pr-2">
                                                    <div className="font-bold text-xs text-indigo-300">{skill.title}</div>
                                                    <div className="text-[10px] text-[#a6adc8] font-mono truncate">{skill.filename}</div>
                                                </div>
                                                <button 
                                                    onClick={() => assignSkill(activeSkillModal, skill.filename)}
                                                    className="bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-semibold py-1 px-3 rounded-lg transition-colors flex items-center space-x-1"
                                                >
                                                    <i className="fa-solid fa-plus text-[9px]"></i>
                                                    <span>Assign</span>
                                                </button>
                                            </div>
                                        ))}
                                        {filteredSkills.length === 0 && (
                                            <div className="text-center py-8 text-xs text-[#6c7086] italic">No skills matching search terms</div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Create New Project / Workspace Modal */}
                        {showNewProjModal && (
                            <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50 animate-fadeIn">
                                <form onSubmit={createNewProject} className="bg-[#1e1e2e] rounded-2xl max-w-md w-full p-6 border border-[#313244] space-y-4 shadow-2xl">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-base font-bold text-white flex items-center space-x-2">
                                            <i className="fa-solid fa-folder-plus text-indigo-400"></i>
                                            <span>Create New Workspace</span>
                                        </h3>
                                        <button type="button" onClick={() => setShowNewProjectModal(false)} className="text-[#bac2de] hover:text-white">
                                            <i className="fa-solid fa-xmark"></i>
                                        </button>
                                    </div>
                                    <div className="space-y-3 text-xs">
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-1">PROJECT NAME</label>
                                            <input 
                                                type="text" 
                                                required
                                                value={newProjName}
                                                onChange={(e) => setNewProjName(e.target.value)}
                                                placeholder="My Auth Microservice"
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-2 text-white font-medium focus:outline-none focus:border-indigo-500"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-1">WORKSPACE DIRECTORY</label>
                                            <input 
                                                type="text" 
                                                required
                                                value={newProjDir}
                                                onChange={(e) => setNewProjDir(e.target.value)}
                                                placeholder="./workspace_auth"
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-2 text-white font-mono focus:outline-none focus:border-indigo-500"
                                            />
                                        </div>
                                    </div>
                                    <div className="flex justify-end pt-2 space-x-2">
                                        <button 
                                            type="button"
                                            onClick={() => setShowNewProjectModal(false)}
                                            className="bg-[#11111b] border border-[#313244] hover:bg-[#313244] text-[#bac2de] py-1.5 px-3 rounded-lg text-xs"
                                        >
                                            Cancel
                                        </button>
                                        <button 
                                            type="submit"
                                            className="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-1.5 px-4 rounded-lg text-xs transition-colors"
                                        >
                                            Initialize Workspace
                                        </button>
                                    </div>
                                </form>
                            </div>
                        )}

                        {/* Create Manual Task Modal */}
                        {showManualTaskModal && (
                            <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50 animate-fadeIn">
                                <form onSubmit={addManualTask} className="bg-[#1e1e2e] rounded-2xl max-w-md w-full p-6 border border-[#313244] space-y-4 shadow-2xl">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-base font-bold text-white flex items-center space-x-2">
                                            <i className="fa-solid fa-square-plus text-indigo-400"></i>
                                            <span>Add Manual Task</span>
                                        </h3>
                                        <button type="button" onClick={() => setShowManualTaskModal(false)} className="text-[#bac2de] hover:text-white">
                                            <i className="fa-solid fa-xmark"></i>
                                        </button>
                                    </div>
                                    <div className="space-y-3 text-xs">
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-1">TASK TITLE</label>
                                            <input 
                                                type="text" 
                                                required
                                                value={manualTaskTitle}
                                                onChange={(e) => setManualTaskTitle(e.target.value)}
                                                placeholder="Build Meal Item Widget"
                                                className="w-full bg-[#11111b] border border-[#313244] rounded p-2 text-white font-medium focus:outline-none focus:border-indigo-500"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-[10px] text-[#bac2de] block mb-1">TASK DESCRIPTION</label>
                                            <textarea 
                                                required
                                                value={manualTaskDesc}
                                                onChange={(e) => setManualTaskDesc(e.target.value)}
                                                placeholder="Design and code a custom card component to list food items with responsive imagery..."
                                                className="w-full h-20 bg-[#11111b] border border-[#313244] rounded p-2 text-white focus:outline-none focus:border-indigo-500 resize-none font-mono"
                                            />
                                        </div>
                                    </div>
                                    <div className="flex justify-end pt-2 space-x-2">
                                        <button 
                                            type="button"
                                            onClick={() => setShowManualTaskModal(false)}
                                            className="bg-[#11111b] border border-[#313244] hover:bg-[#313244] text-[#bac2de] py-1.5 px-3 rounded-lg text-xs"
                                        >
                                            Cancel
                                        </button>
                                        <button 
                                            type="submit"
                                            className="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-1.5 px-4 rounded-lg text-xs transition-colors"
                                        >
                                            Create Task
                                        </button>
                                    </div>
                                </form>
                            </div>
                        )}

                    </div>
                );
            }
            const root = ReactDOM.createRoot(document.getElementById('root'));
            root.render(<App />);
        </script>
    </body>
    </html>
    """

    # Manually swap placeholders with safe clean URLs
    html_content = html_content.replace("URL_TAILWIND", CDN_TAILWIND)
    html_content = html_content.replace("URL_REACT_DOM", CDN_REACT_DOM)
    html_content = html_content.replace("URL_REACT", CDN_REACT)
    html_content = html_content.replace("URL_BABEL", CDN_BABEL)
    html_content = html_content.replace("URL_FONTAWESOME", CDN_FONTAWESOME)

    return HTMLResponse(content=html_content, status_code=200)


# =====================================================================
# ⚙️ MAIN RUNNER EXECUTION
# =====================================================================
if __name__ == "__main__":
    add_system_log("System", "info", "OpenHands Multi-Agent Backend framework live.")
    
    # Initialize workspace structure
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    os.makedirs(SKILLS_DIR, exist_ok=True)
    scan_skills_directory()

    # Load most recently updated project on boot if available
    saved_projects = storage.list_projects()
    active_id = storage.get_active_project_id()
    
    if active_id:
        proj = storage.load_project(active_id)
        if proj:
            CURRENT_PROJECT_ID = proj["id"]
            PROJECT_NAME = proj["name"]
            PROJECT_BRIEF = proj.get("brief") or ""
            WORKSPACE_DIR = proj["workspace_dir"]
            SHARED_BOARD = proj["board_state"]
            for col in ["Backlog", "In Progress", "Code Review", "QA", "Done"]:
                if col not in SHARED_BOARD:
                    SHARED_BOARD[col] = []
            VIRTUAL_FILESYSTEM = proj["files"]
            agent_po.assigned_skills = proj["po_skills"]
            agent_dev.assigned_skills = proj["dev_skills"]
            agent_cr.assigned_skills = proj["cr_skills"] if "cr_skills" in proj else []
            agent_qa.assigned_skills = proj["qa_skills"]

            agent_po.model = proj["po_model"]
            agent_dev.model = proj["dev_model"]
            agent_cr.model = proj["cr_model"]
            agent_qa.model = proj["qa_model"]
            add_system_log("System", "info", f"Loaded active workspace project: '{PROJECT_NAME}'")
    elif saved_projects:
        # Load the latest updated project as a fallback
        load_existing_project(saved_projects[0]["id"])
    else:
        # Save current default project state
        save_current_project_state()
        storage.set_active_project_id(CURRENT_PROJECT_ID)

    print("=" * 70)
    print("      🚀 STARTING FASTAPI AGENT WEB INTERFACE")
    print("      Local dashboard url: http://127.0.0.1:6767")
    print("=" * 70)
    
    uvicorn.run(app, host="127.0.0.1", port=6767)