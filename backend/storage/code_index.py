"""Qdrant-backed semantic codebase index with Ollama embeddings."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import requests

from backend import state
from backend.services.events import publish_event
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import scan_indexable_workspace_files, sync_virtual_filesystem_from_disk

EMBED_DIM = 768
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _publish_index_progress(
    *,
    phase: str,
    files_done: int = 0,
    files_total: int = 0,
    chunks: int = 0,
    current_file: str = "",
    embed_failures: int = 0,
) -> None:
    publish_event(
        "index_progress",
        {
            "phase": phase,
            "filesDone": files_done,
            "filesTotal": files_total,
            "chunks": chunks,
            "currentFile": current_file,
            "embedFailures": embed_failures,
        },
    )


class CodeIndexEngine:
    def __init__(
        self,
        project_id: Optional[str] = None,
        ollama_url: Optional[str] = None,
        qdrant_url: Optional[str] = None,
    ):
        self.project_id = project_id or state.CURRENT_PROJECT_ID
        ws = get_workflow_settings(self.project_id)
        self.ollama_url = (ollama_url or "http://localhost:11434").rstrip("/")
        from backend.services.qdrant_auth import qdrant_connection_settings

        self.qdrant_url, self.qdrant_api_key = qdrant_connection_settings(self.project_id)
        if qdrant_url:
            self.qdrant_url = qdrant_url.rstrip("/")
        self.embed_model = ws.get("embedModel") or "nomic-embed-text"
        self._client = None
        self._available: Optional[bool] = None

    def _collection_name(self) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in self.project_id)
        return f"code_{safe}"

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import Distance, VectorParams

            kwargs: Dict[str, Any] = {"url": self.qdrant_url, "timeout": 10}
            if self.qdrant_api_key:
                kwargs["api_key"] = self.qdrant_api_key
            self._client = QdrantClient(**kwargs)
            name = self._collection_name()
            collections = [c.name for c in self._client.get_collections().collections]
            if name not in collections:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
                )
            self._available = True
            return self._client
        except Exception as exc:
            self._available = False
            add_system_log("System", "warning", f"Qdrant unavailable: {exc}")
            return None

    def _verify_embed_model(self) -> Optional[str]:
        """Return error message if embed model unavailable, else None."""
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=10)
            if resp.status_code != 200:
                return f"Ollama unreachable at {self.ollama_url} (HTTP {resp.status_code})"
            names = {m.get("name") for m in resp.json().get("models", []) if m.get("name")}
            base = self.embed_model.split(":")[0]
            if self.embed_model not in names and not any(n.startswith(base) for n in names):
                return (
                    f"Embed model '{self.embed_model}' not found in Ollama. "
                    f"Run: ollama pull {self.embed_model}"
                )
        except requests.RequestException as exc:
            return f"Cannot reach Ollama at {self.ollama_url}: {exc}"

        test = self._embed("index preflight test")
        if not test:
            return (
                f"Embedding test failed for '{self.embed_model}' at {self.ollama_url}. "
                f"Ensure the model is pulled and Ollama is running."
            )
        return None

    def _embed(self, text: str) -> Optional[List[float]]:
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.embed_model, "prompt": text[:4000]},
                timeout=60,
            )
            if resp.status_code == 200:
                emb = resp.json().get("embedding")
                if isinstance(emb, list) and len(emb) >= 64:
                    return emb
        except requests.RequestException:
            pass
        return None

    @staticmethod
    def _chunk_file(path: str, content: str) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        if not content.strip():
            return chunks
        lines = content.splitlines(keepends=True)
        buf = ""
        start_line = 1
        cur_line = 1
        for line in lines:
            if len(buf) + len(line) > CHUNK_SIZE and buf:
                chunks.append({"path": path, "startLine": start_line, "endLine": cur_line - 1, "content": buf})
                buf = buf[-CHUNK_OVERLAP:] if len(buf) > CHUNK_OVERLAP else ""
                start_line = max(1, cur_line - buf.count("\n"))
            buf += line
            cur_line += 1
        if buf.strip():
            chunks.append({"path": path, "startLine": start_line, "endLine": cur_line - 1, "content": buf})
        return chunks

    def index_workspace(self) -> Dict[str, Any]:
        ws_settings = get_workflow_settings(self.project_id)
        if not ws_settings.get("enableSemanticSearch", True):
            return {"ok": False, "error": "Semantic search disabled in workflow settings"}

        _publish_index_progress(phase="preflight")
        embed_error = self._verify_embed_model()
        if embed_error:
            _publish_index_progress(phase="error")
            return {"ok": False, "error": embed_error}

        client = self._get_client()
        if client is None:
            _publish_index_progress(phase="error")
            return {"ok": False, "error": "Qdrant unavailable"}

        sync_virtual_filesystem_from_disk()
        indexable_files, files_skipped = scan_indexable_workspace_files()
        files_total = len(indexable_files)
        if files_total == 0:
            _publish_index_progress(phase="done", files_total=0, chunks=0)
            return {
                "ok": False,
                "error": (
                    "No indexable text files found in workspace. "
                    "Check WORKSPACE DIR and ensure code files exist."
                ),
                "filesScanned": 0,
                "filesSkipped": files_skipped,
                "chunks": 0,
                "embedFailures": 0,
            }

        name = self._collection_name()
        try:
            client.delete_collection(name)
            from qdrant_client.http.models import Distance, VectorParams

            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )
        except Exception:
            pass

        from qdrant_client.http.models import PointStruct

        indexed = 0
        embed_failures = 0
        points: List[PointStruct] = []
        files_done = 0

        for path, content in indexable_files.items():
            files_done += 1
            _publish_index_progress(
                phase="indexing",
                files_done=files_done,
                files_total=files_total,
                chunks=indexed,
                current_file=path,
                embed_failures=embed_failures,
            )
            for chunk in self._chunk_file(path, content):
                emb = self._embed(chunk["content"])
                if not emb:
                    embed_failures += 1
                    continue
                point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.project_id}:{path}:{chunk['startLine']}")
                points.append(
                    PointStruct(
                        id=str(point_id),
                        vector=emb,
                        payload={
                            "path": path,
                            "startLine": chunk["startLine"],
                            "endLine": chunk["endLine"],
                            "content": chunk["content"][:2000],
                            "projectId": self.project_id,
                        },
                    )
                )
                if len(points) >= 32:
                    client.upsert(collection_name=name, points=points)
                    indexed += len(points)
                    points = []

        if points:
            client.upsert(collection_name=name, points=points)
            indexed += len(points)

        _publish_index_progress(
            phase="done",
            files_done=files_total,
            files_total=files_total,
            chunks=indexed,
            embed_failures=embed_failures,
        )

        if indexed == 0:
            msg = (
                f"Indexed 0 chunks from {files_total} file(s). "
                f"{embed_failures} embedding failure(s). "
                f"Verify Ollama embed model '{self.embed_model}'."
            )
            add_system_log("System", "warning", msg)
            return {
                "ok": False,
                "error": msg,
                "filesScanned": files_total,
                "filesSkipped": files_skipped,
                "chunks": 0,
                "embedFailures": embed_failures,
                "collection": name,
            }

        add_system_log(
            "System",
            "success",
            f"Indexed {indexed} chunks from {files_total} files in Qdrant",
        )
        return {
            "ok": True,
            "chunks": indexed,
            "filesScanned": files_total,
            "filesSkipped": files_skipped,
            "embedFailures": embed_failures,
            "collection": name,
        }

    def upsert_file(self, path: str, content: str) -> None:
        ws_settings = get_workflow_settings(self.project_id)
        if not ws_settings.get("enableSemanticSearch", True):
            return
        client = self._get_client()
        if client is None:
            return

        from qdrant_client.http.models import FieldCondition, Filter, MatchValue, PointStruct

        name = self._collection_name()
        try:
            client.delete(
                collection_name=name,
                points_selector=Filter(
                    must=[FieldCondition(key="path", match=MatchValue(value=path))]
                ),
            )
        except Exception:
            pass

        points: List[PointStruct] = []
        for chunk in self._chunk_file(path, content):
            emb = self._embed(chunk["content"])
            if not emb:
                continue
            point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.project_id}:{path}:{chunk['startLine']}")
            points.append(
                PointStruct(
                    id=str(point_id),
                    vector=emb,
                    payload={
                        "path": path,
                        "startLine": chunk["startLine"],
                        "endLine": chunk["endLine"],
                        "content": chunk["content"][:2000],
                        "projectId": self.project_id,
                    },
                )
            )
        if points:
            client.upsert(collection_name=name, points=points)

    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        ws_settings = get_workflow_settings(self.project_id)
        if not ws_settings.get("enableSemanticSearch", True):
            return []

        client = self._get_client()
        if client is None:
            return []

        emb = self._embed(query)
        if not emb:
            return []

        name = self._collection_name()
        try:
            hits = client.search(collection_name=name, query_vector=emb, limit=limit)
        except Exception:
            return []

        results: List[Dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                {
                    "path": payload.get("path"),
                    "startLine": payload.get("startLine"),
                    "endLine": payload.get("endLine"),
                    "content": payload.get("content", "")[:300],
                    "score": float(hit.score or 0),
                }
            )
        return results

    def index_status(self) -> Dict[str, Any]:
        client = self._get_client()
        if client is None:
            return {"ok": False, "available": False, "chunks": 0}
        name = self._collection_name()
        try:
            info = client.get_collection(name)
            return {
                "ok": True,
                "available": True,
                "collection": name,
                "chunks": info.points_count or 0,
                "qdrantUrl": self.qdrant_url,
                "apiKeyConfigured": bool(self.qdrant_api_key),
            }
        except Exception as exc:
            return {"ok": False, "available": False, "error": str(exc), "chunks": 0}


def format_semantic_search_results(query: str, limit: int = 8) -> str:
    engine = CodeIndexEngine()
    results = engine.search(query, limit=limit)
    if not results:
        return f"No semantic matches for '{query}' (is Qdrant running and index built?)."
    lines = [f"Semantic search '{query}' ({len(results)} hit(s)):"]
    for r in results:
        loc = f"{r.get('path')}:{r.get('startLine')}-{r.get('endLine')}"
        score = r.get("score", 0)
        snippet = str(r.get("content", "")).replace("\n", " ")[:120]
        lines.append(f"- [{score:.3f}] {loc}: {snippet}")
    return "\n".join(lines)


def build_semantic_sprint_context(
    task: Dict[str, Any],
    max_chars: int = 4000,
) -> tuple[str, List[str]]:
    """Inject top semantic index chunks for a sprint task when index is available."""
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    if not ws.get("enableSemanticSearch", True) or ws.get("enableSemanticSprintContext", True) is False:
        return "", []

    engine = CodeIndexEngine()
    status = engine.index_status()
    if not status.get("chunks"):
        return "", []

    title = str(task.get("title") or "").strip()
    desc = str(task.get("description") or "").strip()
    query = "\n".join(part for part in (title, desc) if part)[:600]
    if not query:
        return "", []

    results = engine.search(query, limit=5)
    if not results:
        return "", []

    header = "\n=== SEMANTIC CODE CONTEXT (from index) ===\n"
    blocks: List[str] = []
    paths: List[str] = []
    used = len(header)

    for hit in results:
        path = str(hit.get("path") or "")
        start = hit.get("startLine")
        end = hit.get("endLine")
        score = hit.get("score", 0)
        content = str(hit.get("content") or "")
        block = (
            f"--- {path}:{start}-{end} (relevance {score:.3f}) ---\n"
            f"{content}\n--- END {path} ---"
        )
        if used + len(block) > max_chars and blocks:
            break
        if used + len(block) > max_chars:
            remaining = max_chars - used - 40
            if remaining > 200:
                block = block[:remaining] + "\n...[truncated]\n"
            else:
                break
        blocks.append(block)
        used += len(block)
        if path and path not in paths:
            paths.append(path)

    if not blocks:
        return "", []
    return header + "\n\n".join(blocks) + "\n", paths
