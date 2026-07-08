import json
import math
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

import requests

from backend.config import DB_PATH


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
    ) -> None:
        self.save(agent_id, content, category, project_id=project_id)

    def search(
        self,
        agent_id: str,
        query: str,
        limit: int = 3,
        *,
        project_id: Optional[str] = None,
        include_all_agents: bool = False,
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
                scored.sort(key=lambda x: x["score"], reverse=True)
                return scored[:limit]

        tfidf = self._tfidf_search(query, records, limit)
        for item in tfidf:
            item.setdefault("agent", agent_id.split(":")[-1] if ":" in agent_id else agent_id)
        return tfidf

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
