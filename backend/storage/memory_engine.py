import json
import math
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

import requests

from backend.config import DB_PATH


CORE_BLOCK_CATEGORY = "core_block"
DEV_CORE_KEY = "dev_core"
CORE_BLOCK_MAX_CHARS = 800


def resolve_embed_model(explicit: Optional[str] = None) -> str:
    """Resolve Ollama embed model from workflow settings or explicit override."""
    if explicit:
        return explicit
    from backend.services.workflow_settings import get_workflow_settings

    return str(get_workflow_settings().get("embedModel") or "nomic-embed-text")


def create_memory_engine(
    ollama_url: str = "http://localhost:11434",
    embed_model: Optional[str] = None,
) -> "SemanticMemoryEngine":
    return SemanticMemoryEngine(
        ollama_url=ollama_url.rstrip("/"),
        embed_model=resolve_embed_model(embed_model),
    )


class SemanticMemoryEngine:
    """
    SQLite-backed semantic memory with Ollama embeddings when available,
    falling back to TF-IDF cosine similarity.
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        ollama_url: str = "http://localhost:11434",
        embed_model: str = "nomic-embed-text",
    ):
        self.db_path = db_path
        self.ollama_url = ollama_url.rstrip("/")
        self.embed_model = resolve_embed_model(embed_model)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    category TEXT,
                    content TEXT,
                    embedding TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def _embed_ollama(self, text: str) -> Optional[List[float]]:
        try:
            response = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.embed_model, "prompt": text},
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                embedding = data.get("embedding")
                if isinstance(embedding, list) and embedding:
                    return embedding
        except requests.RequestException:
            pass
        return None

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _tfidf_search(query: str, records: List[sqlite3.Row], limit: int) -> List[Dict[str, Any]]:
        def get_words(text: str) -> List[str]:
            return re.sub(r"[^\w\s]", "", text.lower()).split()

        query_words = get_words(query)
        scored_records: List[Dict[str, Any]] = []

        for record in records:
            doc_words = get_words(record["content"])
            all_unique_words = list(set(query_words + doc_words))
            if not all_unique_words:
                continue

            v_q = [query_words.count(w) for w in all_unique_words]
            v_d = [doc_words.count(w) for w in all_unique_words]

            dot_product = sum(a * b for a, b in zip(v_q, v_d))
            mag_q = math.sqrt(sum(a * a for a in v_q))
            mag_d = math.sqrt(sum(b * b for b in v_d))

            similarity = dot_product / (mag_q * mag_d) if (mag_q * mag_d) > 0 else 0.0

            scored_records.append(
                {
                    "id": record["id"],
                    "category": record["category"],
                    "content": record["content"],
                    "timestamp": record["timestamp"],
                    "score": similarity,
                }
            )

        scored_records.sort(key=lambda x: x["score"], reverse=True)
        return scored_records[:limit]

    @staticmethod
    def _normalize_content_key(content: str) -> str:
        """Normalize memory content for deduplication (matches search() behavior)."""
        return str(content or "").strip()[:200]

    def _scoped_agent_id(self, agent_id: str, project_id: Optional[str] = None) -> str:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        if agent_id.startswith(f"{pid}:"):
            return agent_id
        return f"{pid}:{agent_id}"

    def _project_shared_scope(self, project_id: Optional[str] = None) -> str:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        return f"{pid}:__project__"

    def save_project_note(
        self,
        content: str,
        category: str = "user_note",
        *,
        project_id: Optional[str] = None,
    ) -> None:
        """Save a note visible to all agents via shared project scope."""
        self.save("__project__", content, category, project_id=project_id)

    def save(
        self,
        agent_id: str,
        content: str,
        category: str = "general",
        *,
        project_id: Optional[str] = None,
    ) -> None:
        scoped = self._scoped_agent_id(agent_id, project_id)
        text = content.strip()
        if not text:
            return
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM memories
                WHERE agent_id = ? AND category = ? AND TRIM(content) = ?
                LIMIT 1
                """,
                (scoped, category, text),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE memories SET timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                    (existing[0],),
                )
                conn.commit()
                return
            mem_id = str(uuid.uuid4())
            embedding = self._embed_ollama(text)
            embedding_json = json.dumps(embedding) if embedding else None
            conn.execute(
                "INSERT INTO memories (id, agent_id, category, content, embedding) VALUES (?, ?, ?, ?, ?)",
                (mem_id, scoped, category, text, embedding_json),
            )
            conn.commit()

    def save_outcome(
        self,
        agent_id: str,
        content: str,
        category: str,
        *,
        project_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        text = content.strip()
        if meta:
            payload = {"lesson": text, **meta}
            text = json.dumps(payload, ensure_ascii=False)[:4000]
        self.save(agent_id, text, category, project_id=project_id)

    def save_step_lesson(
        self,
        agent_id: str,
        *,
        lesson: str,
        stop_reason: str = "",
        tools_used: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        files: Optional[List[str]] = None,
        project_id: Optional[str] = None,
    ) -> None:
        """Structured end-of-step lesson (preferred over per-tool breadcrumbs)."""
        meta: Dict[str, Any] = {
            "kind": "step_lesson",
            "stopReason": stop_reason or "",
            "taskId": task_id or "",
            "toolsUsed": list(tools_used or [])[:20],
            "files": list(files or [])[:12],
        }
        category = "failure" if stop_reason in (
            "duplicate_tool",
            "tool_failure_stop",
            "step_timeout",
            "plan_exhausted",
            "read_only_no_edits",
        ) else "fix_pattern"
        self.save_outcome(
            agent_id,
            lesson[:800],
            category,
            project_id=project_id,
            meta=meta,
        )
        # Sticky Dev core block (Letta-inspired pinned memory — always injected).
        try:
            from backend.services.workflow_settings import get_workflow_settings

            if get_workflow_settings().get("enableDevCoreMemoryBlock", True):
                role = agent_id.split(":")[-1] if ":" in agent_id else agent_id
                if role in ("Developer", "dev"):
                    bullet = lesson.strip().split("\n")[0][:200]
                    if bullet:
                        self.merge_lesson_into_core_block(
                            "Developer",
                            bullet,
                            project_id=project_id,
                        )
        except Exception:
            pass

    def core_block_memory_id(self, agent_id: str = "Developer", *, project_id: Optional[str] = None) -> str:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        role = agent_id.split(":")[-1] if ":" in agent_id else agent_id
        key = DEV_CORE_KEY if role in ("Developer", "dev") else f"{role.lower().replace(' ', '_')}_core"
        return f"{pid}-{key}"

    def get_core_block(
        self,
        agent_id: str = "Developer",
        *,
        project_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        mem_id = self.core_block_memory_id(agent_id, project_id=project_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, agent_id, category, content, timestamp FROM memories WHERE id = ?",
                (mem_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        agent_scope = str(row["agent_id"] or "")
        display_agent = agent_scope.split(":")[-1] if ":" in agent_scope else agent_scope
        return {
            "id": row["id"],
            "agent": display_agent,
            "category": row["category"],
            "content": row["content"],
            "timestamp": row["timestamp"],
        }

    def upsert_core_block(
        self,
        content: str,
        agent_id: str = "Developer",
        *,
        project_id: Optional[str] = None,
        max_chars: int = CORE_BLOCK_MAX_CHARS,
    ) -> Dict[str, Any]:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        text = str(content or "").strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        mem_id = self.core_block_memory_id(agent_id, project_id=pid)
        scoped = self._scoped_agent_id(agent_id if agent_id != "dev" else "Developer", pid)
        embedding = self._embed_ollama(text) if text else None
        embedding_json = json.dumps(embedding) if embedding else None
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM memories WHERE id = ?", (mem_id,))
            if cursor.fetchone():
                cursor.execute(
                    """
                    UPDATE memories
                    SET content = ?, category = ?, embedding = ?, timestamp = CURRENT_TIMESTAMP,
                        agent_id = ?
                    WHERE id = ?
                    """,
                    (text, CORE_BLOCK_CATEGORY, embedding_json, scoped, mem_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO memories (id, agent_id, category, content, embedding)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (mem_id, scoped, CORE_BLOCK_CATEGORY, text, embedding_json),
                )
            conn.commit()
        return self.get_core_block(agent_id, project_id=pid) or {
            "id": mem_id,
            "agent": agent_id,
            "category": CORE_BLOCK_CATEGORY,
            "content": text,
            "timestamp": None,
        }

    def merge_lesson_into_core_block(
        self,
        agent_id: str,
        lesson_bullet: str,
        *,
        project_id: Optional[str] = None,
        max_chars: int = CORE_BLOCK_MAX_CHARS,
    ) -> Dict[str, Any]:
        """Append a short bullet; drop oldest bullets until under max_chars."""
        raw = str(lesson_bullet or "").strip()
        if not raw:
            existing = self.get_core_block(agent_id, project_id=project_id)
            return existing or {"id": "", "content": "", "category": CORE_BLOCK_CATEGORY}
        if raw.startswith("- "):
            bullet = raw
        else:
            bullet = f"- {raw}"
        bullet = bullet[:220]
        existing = self.get_core_block(agent_id, project_id=project_id)
        lines: List[str] = []
        if existing and str(existing.get("content") or "").strip():
            for line in str(existing["content"]).splitlines():
                line = line.strip()
                if line:
                    lines.append(line if line.startswith("- ") else f"- {line}")
        # Dedupe exact bullet
        if bullet not in lines:
            lines.append(bullet)
        while lines and len("\n".join(lines)) > max_chars:
            lines.pop(0)
        merged = "\n".join(lines)
        return self.upsert_core_block(merged, agent_id, project_id=project_id, max_chars=max_chars)

    def search(
        self,
        agent_id: str,
        query: str,
        limit: int = 3,
        *,
        project_id: Optional[str] = None,
        include_all_agents: bool = False,
        prefer_categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        scoped = self._scoped_agent_id(agent_id, pid)
        shared = self._project_shared_scope(pid)
        prefix = f"{pid}:"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if include_all_agents:
                cursor.execute(
                    """
                    SELECT id, category, content, embedding, timestamp, agent_id
                    FROM memories WHERE agent_id LIKE ?
                    """,
                    (f"{prefix}%",),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, category, content, embedding, timestamp, agent_id
                    FROM memories
                    WHERE agent_id IN (?, ?)
                    """,
                    (scoped, shared),
                )
            records = cursor.fetchall()

        if not records:
            legacy = agent_id.split(":")[-1] if ":" in agent_id else agent_id
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, category, content, embedding, timestamp, agent_id
                    FROM memories WHERE agent_id = ?
                    """,
                    (legacy,),
                )
                records = cursor.fetchall()

        if not records:
            return []

        # Core blocks are always injected separately — exclude from semantic search.
        records = [r for r in records if str(r["category"] or "").lower() != CORE_BLOCK_CATEGORY]
        if not records:
            return []

        seen_content: set[str] = set()
        deduped: List[sqlite3.Row] = []
        for record in records:
            key = self._normalize_content_key(str(record["content"] or ""))
            if key in seen_content:
                continue
            seen_content.add(key)
            deduped.append(record)
        records = deduped

        query_embedding = self._embed_ollama(query)
        if query_embedding:
            scored: List[Dict[str, Any]] = []
            for record in records:
                if not record["embedding"]:
                    continue
                try:
                    stored = json.loads(record["embedding"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(stored, list):
                    continue
                score = self._cosine_similarity(query_embedding, stored)
                agent_scope = str(record["agent_id"] or "")
                display_agent = agent_scope.split(":")[-1] if ":" in agent_scope else agent_scope
                scored.append(
                    {
                        "id": record["id"],
                        "category": record["category"],
                        "content": record["content"],
                        "timestamp": record["timestamp"],
                        "agent": display_agent,
                        "score": score,
                    }
                )
            if scored:
                if prefer_categories:
                    prefer = {c.lower() for c in prefer_categories}
                    for item in scored:
                        cat = str(item.get("category") or "").lower()
                        if cat in prefer:
                            item["score"] = float(item.get("score") or 0) + 0.08
                scored.sort(key=lambda x: x["score"], reverse=True)
                return scored[:limit]

        tfidf = self._tfidf_search(query, records, limit * 2 if prefer_categories else limit)
        for item in tfidf:
            item.setdefault("agent", agent_id.split(":")[-1] if ":" in agent_id else agent_id)
        if prefer_categories:
            prefer = {c.lower() for c in prefer_categories}
            for item in tfidf:
                cat = str(item.get("category") or "").lower()
                if cat in prefer:
                    item["score"] = float(item.get("score") or 0) + 0.08
            tfidf.sort(key=lambda x: x["score"], reverse=True)
        return tfidf[:limit]

    def list_for_project(
        self,
        *,
        project_id: Optional[str] = None,
        agent: Optional[str] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        dedupe: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        prefix = f"{pid}:"
        clauses: List[str] = []
        params: List[Any] = []

        if agent:
            scoped = self._scoped_agent_id(agent, pid)
            clauses.append("agent_id = ?")
            params.append(scoped)
        else:
            clauses.append("agent_id LIKE ?")
            params.append(f"{prefix}%")

        if category:
            clauses.append("category = ?")
            params.append(category)

        if q and q.strip():
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{q.strip().lower()}%")

        where_sql = " AND ".join(clauses)
        fetch_limit = min(max(limit * 4, limit), 800) if dedupe else min(limit, 200)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, agent_id, category, content, timestamp
                FROM memories
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (*params, fetch_limit),
            )
            rows = cursor.fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            agent_id = str(row["agent_id"] or "")
            display_agent = agent_id.split(":")[-1] if ":" in agent_id else agent_id
            out.append(
                {
                    "id": row["id"],
                    "agent": display_agent,
                    "category": row["category"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
            )

        if not dedupe:
            return out[:limit]

        grouped: Dict[str, Dict[str, Any]] = {}
        group_order: List[str] = []
        for entry in out:
            key = self._normalize_content_key(entry.get("content", ""))
            if key not in grouped:
                grouped[key] = {
                    **entry,
                    "duplicateCount": 1,
                    "duplicateIds": [entry["id"]],
                }
                group_order.append(key)
            else:
                group = grouped[key]
                group["duplicateCount"] = int(group.get("duplicateCount", 1)) + 1
                group["duplicateIds"].append(entry["id"])
                if str(entry.get("timestamp", "")) > str(group.get("timestamp", "")):
                    group["id"] = entry["id"]
                    group["timestamp"] = entry["timestamp"]
                    group["agent"] = entry["agent"]
                    group["category"] = entry["category"]
                    group["content"] = entry["content"]

        return [grouped[key] for key in group_order][:limit]

    def delete(self, memory_id: str, *, project_id: Optional[str] = None) -> bool:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        prefix = f"{pid}:"
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT agent_id FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return False
            agent_id = str(row[0] or "")
            if not agent_id.startswith(prefix) and ":" in agent_id:
                return False
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update(
        self,
        memory_id: str,
        content: str,
        *,
        category: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        prefix = f"{pid}:"
        text = content.strip()
        if not text:
            return False
        embedding = self._embed_ollama(text)
        embedding_json = json.dumps(embedding) if embedding else None
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT agent_id FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return False
            agent_id = str(row[0] or "")
            if not agent_id.startswith(prefix) and ":" in agent_id:
                return False
            if category is not None:
                cursor.execute(
                    """
                    UPDATE memories
                    SET content = ?, category = ?, embedding = ?, timestamp = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (text, category, embedding_json, memory_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE memories
                    SET content = ?, embedding = ?, timestamp = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (text, embedding_json, memory_id),
                )
            conn.commit()
            return cursor.rowcount > 0
