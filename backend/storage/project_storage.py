import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.config import DB_PATH


class ProjectStorage:
    """Manages workspace projects, board columns, logs, chat, and file revisions."""

    def __init__(self, db_path: str = DB_PATH):
        from backend.config import migrate_legacy_database

        migrate_legacy_database()
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
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
                    project_logs TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            try:
                conn.execute("ALTER TABLE projects ADD COLUMN project_logs TEXT")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    agent TEXT,
                    content TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_revisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    previous_content TEXT,
                    author TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS brief_changelog (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    snippet TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def save_project(
        self,
        proj_id: str,
        name: str,
        brief: str,
        workspace_dir: str,
        board_state: dict,
        files: dict,
        po_skills: list,
        dev_skills: list,
        cr_skills: list,
        qa_skills: list,
        po_model: str,
        dev_model: str,
        cr_model: str,
        qa_model: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, brief, workspace_dir, board_state, virtual_filesystem,
                    po_skills, dev_skills, cr_skills, qa_skills,
                    po_model, dev_model, cr_model, qa_model, updated_at
                )
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
                """,
                (
                    proj_id,
                    name,
                    brief,
                    workspace_dir,
                    json.dumps(board_state),
                    json.dumps(files),
                    json.dumps(po_skills),
                    json.dumps(dev_skills),
                    json.dumps(cr_skills),
                    json.dumps(qa_skills),
                    po_model,
                    dev_model,
                    cr_model,
                    qa_model,
                ),
            )
            conn.commit()

    def load_project(self, proj_id: str) -> Optional[Dict[str, Any]]:
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
                    "cr_skills": json.loads(row["cr_skills"])
                    if "cr_skills" in row.keys() and row["cr_skills"]
                    else [],
                    "qa_skills": json.loads(row["qa_skills"]) if row["qa_skills"] else [],
                    "po_model": row["po_model"]
                    if "po_model" in row.keys() and row["po_model"]
                    else "llama3:8b",
                    "dev_model": row["dev_model"]
                    if "dev_model" in row.keys() and row["dev_model"]
                    else "qwen2.5-coder:14b",
                    "cr_model": row["cr_model"]
                    if "cr_model" in row.keys() and row["cr_model"]
                    else "qwen2.5-coder:7b",
                    "qa_model": row["qa_model"]
                    if "qa_model" in row.keys() and row["qa_model"]
                    else "qwen2.5-coder:7b",
                }
        return None

    def delete_project(self, proj_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE project_id = ?", (proj_id,))
            cursor.execute("DELETE FROM file_revisions WHERE project_id = ?", (proj_id,))
            cursor.execute("DELETE FROM brief_changelog WHERE project_id = ?", (proj_id,))
            cursor.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_projects(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, updated_at FROM projects ORDER BY updated_at DESC")
            return [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"]} for r in cursor.fetchall()]

    def set_active_project_id(self, proj_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_project_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (proj_id,),
            )
            conn.commit()

    def get_active_project_id(self) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'active_project_id'")
            row = cursor.fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def add_brief_changelog(
        self,
        project_id: str,
        source: str,
        summary: str,
        snippet: str = "",
    ) -> None:
        entry_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO brief_changelog (id, project_id, source, summary, snippet) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry_id, project_id, source, summary, snippet),
            )
            conn.commit()

    def get_brief_changelog(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source, summary, snippet, created_at AS timestamp "
                "FROM brief_changelog WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def save_project_logs(self, proj_id: str, logs: List[Dict[str, str]]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE projects SET project_logs = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(logs), proj_id),
            )
            conn.commit()

    def load_project_logs(self, proj_id: str) -> List[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT project_logs FROM projects WHERE id = ?", (proj_id,))
            row = cursor.fetchone()
            if row and row["project_logs"]:
                try:
                    return json.loads(row["project_logs"])
                except json.JSONDecodeError:
                    return []
        return []

    def save_chat_message(
        self,
        project_id: str,
        role: str,
        content: str,
        agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        msg_id = str(uuid.uuid4())
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO chat_messages (id, project_id, role, agent, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, project_id, role, agent, content, created_at),
            )
            conn.commit()
        return {
            "id": msg_id,
            "project_id": project_id,
            "role": role,
            "agent": agent,
            "content": content,
            "created_at": created_at,
        }

    def get_chat_messages(self, project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, project_id, role, agent, content, created_at "
                "FROM chat_messages WHERE project_id = ? ORDER BY created_at ASC LIMIT ?",
                (project_id, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def save_file_revision(
        self,
        project_id: str,
        path: str,
        content: str,
        previous_content: Optional[str] = None,
        author: Optional[str] = None,
    ) -> Dict[str, Any]:
        rev_id = str(uuid.uuid4())
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO file_revisions (id, project_id, path, content, previous_content, author, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rev_id, project_id, path, content, previous_content, author, created_at),
            )
            conn.commit()
        return {
            "id": rev_id,
            "project_id": project_id,
            "path": path,
            "content": content,
            "previous_content": previous_content,
            "author": author,
            "created_at": created_at,
        }

    def get_file_revisions(self, project_id: str, path: str, limit: int = 20) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, project_id, path, content, previous_content, author, created_at "
                "FROM file_revisions WHERE project_id = ? AND path = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, path, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_latest_revision_for_path(self, project_id: str, path: str) -> Optional[Dict[str, Any]]:
        revisions = self.get_file_revisions(project_id, path, limit=1)
        return revisions[0] if revisions else None

    def get_file_revision(self, revision_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, project_id, path, content, previous_content, author, created_at "
                "FROM file_revisions WHERE id = ?",
                (revision_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
