import json
import math
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

import requests

from backend.config import DB_PATH


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
        self.embed_model = embed_model
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

    def _scoped_agent_id(self, agent_id: str, project_id: Optional[str] = None) -> str:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        if agent_id.startswith(f"{pid}:"):
            return agent_id
        return f"{pid}:{agent_id}"

    def save(
        self,
        agent_id: str,
        content: str,
        category: str = "general",
        *,
        project_id: Optional[str] = None,
    ) -> None:
        scoped = self._scoped_agent_id(agent_id, project_id)
        mem_id = str(uuid.uuid4())
        embedding = self._embed_ollama(content)
        embedding_json = json.dumps(embedding) if embedding else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memories (id, agent_id, category, content, embedding) VALUES (?, ?, ?, ?, ?)",
                (mem_id, scoped, category, content, embedding_json),
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
    ) -> List[Dict[str, Any]]:
        scoped = self._scoped_agent_id(agent_id, project_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, category, content, embedding, timestamp FROM memories WHERE agent_id = ?",
                (scoped,),
            )
            records = cursor.fetchall()

        if not records:
            legacy = agent_id.split(":")[-1] if ":" in agent_id else agent_id
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, category, content, embedding, timestamp FROM memories WHERE agent_id = ?",
                    (legacy,),
                )
                records = cursor.fetchall()

        if not records:
            return []

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
                scored.append(
                    {
                        "id": record["id"],
                        "category": record["category"],
                        "content": record["content"],
                        "timestamp": record["timestamp"],
                        "score": score,
                    }
                )
            if scored:
                scored.sort(key=lambda x: x["score"], reverse=True)
                return scored[:limit]

        return self._tfidf_search(query, records, limit)

    def list_for_project(
        self,
        *,
        project_id: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        from backend import state

        pid = project_id or state.CURRENT_PROJECT_ID or "default-proj"
        prefix = f"{pid}:"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if agent:
                scoped = self._scoped_agent_id(agent, pid)
                cursor.execute(
                    """
                    SELECT id, agent_id, category, content, timestamp
                    FROM memories WHERE agent_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (scoped, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, agent_id, category, content, timestamp
                    FROM memories
                    WHERE agent_id LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (f"{prefix}%", limit),
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
        return out

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
