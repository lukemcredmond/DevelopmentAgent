"""Qdrant-backed semantic codebase index with Ollama embeddings + hybrid lexical fuse."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend import state
from backend.services.events import publish_event
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import scan_indexable_workspace_files, sync_virtual_filesystem_from_disk

DEFAULT_EMBED_DIM = 768
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
RRF_K = 60


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


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8", errors="replace")).hexdigest()[:32]


def _rrf_fuse(
    dense: List[Dict[str, Any]],
    lexical: List[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Reciprocal-rank fusion of dense + lexical hit lists."""
    scores: Dict[str, float] = {}
    best: Dict[str, Dict[str, Any]] = {}

    def _key(hit: Dict[str, Any]) -> str:
        return f"{hit.get('path')}:{hit.get('startLine')}:{hit.get('endLine')}"

    for rank, hit in enumerate(dense):
        key = _key(hit)
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        merged = dict(hit)
        merged["source"] = "dense" if key not in best else "hybrid"
        if key in best and best[key].get("source") == "lexical":
            merged["source"] = "hybrid"
        best[key] = {**best.get(key, {}), **merged}
        best[key]["score"] = scores[key]

    for rank, hit in enumerate(lexical):
        key = _key(hit)
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        prev = best.get(key, {})
        merged = {**prev, **hit}
        merged["source"] = "hybrid" if key in best else "lexical"
        merged["score"] = scores[key]
        # Prefer denser snippet content when present
        if not merged.get("content") and prev.get("content"):
            merged["content"] = prev["content"]
        best[key] = merged

    ordered = sorted(best.values(), key=lambda h: float(h.get("score") or 0), reverse=True)
    return ordered[:limit]


def _mmr_path_diverse(hits: List[Dict[str, Any]], *, top_k: int) -> List[Dict[str, Any]]:
    """Prefer path diversity while keeping score order (simple MMR-style)."""
    if top_k <= 0 or not hits:
        return []
    selected: List[Dict[str, Any]] = []
    used_paths: set[str] = set()
    remaining = list(hits)
    while remaining and len(selected) < top_k:
        # Prefer unused paths first among remaining
        pick_idx = 0
        for i, hit in enumerate(remaining):
            path = str(hit.get("path") or "")
            if path and path not in used_paths:
                pick_idx = i
                break
        chosen = remaining.pop(pick_idx)
        selected.append(chosen)
        path = str(chosen.get("path") or "")
        if path:
            used_paths.add(path)
    return selected


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
        self._embed_dim: Optional[int] = None

    def _collection_name(self) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in self.project_id)
        return f"code_{safe}"

    def _resolve_embed_dim(self) -> int:
        if self._embed_dim:
            return self._embed_dim
        probe = self._embed("index dim probe")
        if probe:
            self._embed_dim = len(probe)
            return self._embed_dim
        self._embed_dim = DEFAULT_EMBED_DIM
        return self._embed_dim

    def _collection_vector_size(self, client) -> Optional[int]:
        name = self._collection_name()
        try:
            info = client.get_collection(name)
            cfg = getattr(info, "config", None)
            params = getattr(getattr(cfg, "params", None), "vectors", None)
            size = getattr(params, "size", None)
            if isinstance(size, int):
                return size
        except Exception:
            pass
        return None

    def _ensure_collection(self, client, *, force_recreate: bool = False) -> None:
        from qdrant_client.http.models import Distance, VectorParams

        name = self._collection_name()
        dim = self._resolve_embed_dim()
        collections = [c.name for c in client.get_collections().collections]
        existing_size = self._collection_vector_size(client) if name in collections else None
        need_create = name not in collections or force_recreate
        if name in collections and existing_size is not None and existing_size != dim:
            add_system_log(
                "System",
                "warning",
                f"Embed dim mismatch ({existing_size} vs {dim}) — recreating collection {name}",
            )
            try:
                client.delete_collection(name)
            except Exception:
                pass
            need_create = True
        if need_create:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient

            kwargs: Dict[str, Any] = {"url": self.qdrant_url, "timeout": 10}
            if self.qdrant_api_key:
                kwargs["api_key"] = self.qdrant_api_key
            self._client = QdrantClient(**kwargs)
            self._ensure_collection(self._client)
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
        self._embed_dim = len(test)
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
                    if self._embed_dim is None:
                        self._embed_dim = len(emb)
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
                chunks.append(
                    {"path": path, "startLine": start_line, "endLine": cur_line - 1, "content": buf}
                )
                buf = buf[-CHUNK_OVERLAP:] if len(buf) > CHUNK_OVERLAP else ""
                start_line = max(1, cur_line - buf.count("\n"))
            buf += line
            cur_line += 1
        if buf.strip():
            chunks.append(
                {"path": path, "startLine": start_line, "endLine": cur_line - 1, "content": buf}
            )
        return chunks

    def _payload_hashes(self, client) -> Dict[str, str]:
        """Map path -> contentHash from existing points (one hash per path)."""
        name = self._collection_name()
        out: Dict[str, str] = {}
        try:
            offset = None
            while True:
                records, offset = client.scroll(
                    collection_name=name,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for rec in records or []:
                    payload = rec.payload or {}
                    path = payload.get("path")
                    h = payload.get("contentHash")
                    if path and h and path not in out:
                        out[str(path)] = str(h)
                if offset is None:
                    break
        except Exception:
            pass
        return out

    def _delete_path(self, client, path: str) -> None:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

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

    def _points_for_file(self, path: str, content: str) -> Tuple[List[Any], int]:
        from qdrant_client.http.models import PointStruct

        file_hash = _content_hash(content)
        points: List[PointStruct] = []
        embed_failures = 0
        for chunk in self._chunk_file(path, content):
            emb = self._embed(chunk["content"])
            if not emb:
                embed_failures += 1
                continue
            point_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"{self.project_id}:{path}:{chunk['startLine']}"
            )
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
                        "contentHash": file_hash,
                    },
                )
            )
        return points, embed_failures

    def index_workspace(self, *, force: bool = False) -> Dict[str, Any]:
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
        dim = self._resolve_embed_dim()
        existing_size = self._collection_vector_size(client)
        recreate = force or (existing_size is not None and existing_size != dim)
        if recreate:
            try:
                client.delete_collection(name)
            except Exception:
                pass
            self._ensure_collection(client, force_recreate=True)
            existing_hashes: Dict[str, str] = {}
        else:
            self._ensure_collection(client)
            existing_hashes = self._payload_hashes(client)

        indexed = 0
        skipped_unchanged = 0
        embed_failures = 0
        files_done = 0
        current_paths = set(indexable_files.keys())

        # Delete paths removed from workspace
        for old_path in list(existing_hashes.keys()):
            if old_path not in current_paths:
                self._delete_path(client, old_path)
                existing_hashes.pop(old_path, None)

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
            new_hash = _content_hash(content)
            if not recreate and existing_hashes.get(path) == new_hash:
                skipped_unchanged += 1
                continue
            self._delete_path(client, path)
            points, fails = self._points_for_file(path, content)
            embed_failures += fails
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

        status = self.index_status()
        raw_chunks = status.get("chunks")
        try:
            total_chunks = int(raw_chunks) if raw_chunks is not None else 0
        except (TypeError, ValueError):
            total_chunks = 0
        if total_chunks == 0 and indexed == 0:
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
                "skippedUnchanged": skipped_unchanged,
                "collection": name,
            }

        add_system_log(
            "System",
            "success",
            f"Indexed {indexed} new/changed chunks "
            f"(skipped {skipped_unchanged} unchanged) from {files_total} files in Qdrant",
        )
        return {
            "ok": True,
            "chunks": total_chunks or indexed,
            "chunksUpserted": indexed,
            "skippedUnchanged": skipped_unchanged,
            "filesScanned": files_total,
            "filesSkipped": files_skipped,
            "embedFailures": embed_failures,
            "collection": name,
            "embedDim": dim,
            "force": force,
        }

    def upsert_file(self, path: str, content: str) -> None:
        ws_settings = get_workflow_settings(self.project_id)
        if not ws_settings.get("enableSemanticSearch", True):
            return
        client = self._get_client()
        if client is None:
            return

        name = self._collection_name()
        self._delete_path(client, path)
        points, _ = self._points_for_file(path, content)
        if points:
            client.upsert(collection_name=name, points=points)

    def _dense_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
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
                    "source": "dense",
                    "denseScore": float(hit.score or 0),
                }
            )
        return results

    def _lexical_search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Lightweight substring/token scan over the virtual filesystem."""
        tokens = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) >= 3][:8]
        if not tokens:
            needle = (query or "").strip().lower()
            if len(needle) < 2:
                return []
            tokens = [needle]

        sync_virtual_filesystem_from_disk()
        scored: List[Dict[str, Any]] = []
        for path, content in (state.VIRTUAL_FILESYSTEM or {}).items():
            if not isinstance(content, str) or not content:
                continue
            lower = content.lower()
            path_l = str(path).lower()
            hits = 0
            for tok in tokens:
                hits += lower.count(tok)
                if tok in path_l:
                    hits += 3
            if hits <= 0:
                continue
            # Snippet around first token
            first = tokens[0]
            idx = lower.find(first)
            start = max(0, idx - 80) if idx >= 0 else 0
            snippet = content[start : start + 300]
            line_no = content[: max(0, idx)].count("\n") + 1 if idx >= 0 else 1
            scored.append(
                {
                    "path": path,
                    "startLine": line_no,
                    "endLine": line_no + snippet.count("\n"),
                    "content": snippet,
                    "score": float(hits),
                    "source": "lexical",
                }
            )
        scored.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
        return scored[:limit]

    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        ws_settings = get_workflow_settings(self.project_id)
        if not ws_settings.get("enableSemanticSearch", True):
            return []

        fetch = max(limit * 2, 8)
        dense = self._dense_search(query, fetch)
        if not ws_settings.get("enableHybridSearch", True):
            return dense[:limit]

        lexical = self._lexical_search(query, fetch)
        if not dense and not lexical:
            return []
        if not dense:
            return lexical[:limit]
        if not lexical:
            return dense[:limit]
        return _rrf_fuse(dense, lexical, limit=limit)

    def index_status(self) -> Dict[str, Any]:
        client = self._get_client()
        if client is None:
            return {"ok": False, "available": False, "chunks": 0}
        name = self._collection_name()
        try:
            info = client.get_collection(name)
            points = getattr(info, "points_count", 0)
            try:
                chunks = int(points) if points is not None else 0
            except (TypeError, ValueError):
                chunks = 0
            return {
                "ok": True,
                "available": True,
                "collection": name,
                "chunks": chunks,
                "qdrantUrl": self.qdrant_url,
                "apiKeyConfigured": bool(self.qdrant_api_key),
                "embedDim": self._collection_vector_size(client) or self._embed_dim,
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
        src = r.get("source") or "dense"
        snippet = str(r.get("content", "")).replace("\n", " ")[:120]
        lines.append(f"- [{score:.3f}|{src}] {loc}: {snippet}")
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

    top_k = max(1, int(ws.get("semanticSprintTopK") or 5))
    min_score = float(ws.get("semanticMinScore") if ws.get("semanticMinScore") is not None else 0.35)
    raw = engine.search(query, limit=max(top_k * 3, 8))
    # Dense cosine scores are typically 0–1; RRF fused scores are small (~0.03).
    # Apply min_score only to dense-only hits; for hybrid/RRF keep relative ranking.
    filtered: List[Dict[str, Any]] = []
    for hit in raw:
        src = hit.get("source") or "dense"
        score = float(hit.get("score") or 0)
        dense_score = hit.get("denseScore")
        if src == "dense" and dense_score is not None:
            if float(dense_score) < min_score:
                continue
        elif src == "dense" and score < min_score:
            continue
        filtered.append(hit)

    results = _mmr_path_diverse(filtered or raw, top_k=top_k)
    if not results:
        return "", []

    # Light retrieval feedback: log when dense hits sit near/under the score floor
    try:
        from backend.services.logs import add_system_log

        weak = 0
        for hit in results:
            ds = hit.get("denseScore")
            sc = float(ds if ds is not None else hit.get("score") or 0)
            if (hit.get("source") or "dense") == "dense" and sc < min_score + 0.05:
                weak += 1
        if weak >= max(1, len(results) // 2):
            task["retrievalFeedback"] = {
                "weakHits": weak,
                "totalHits": len(results),
                "minScore": min_score,
                "note": "semantic context near score floor — may be noisy",
            }
            add_system_log(
                "System",
                "info",
                f"retrieval_feedback task={task.get('id')} weakHits={weak}/{len(results)} "
                f"minScore={min_score}",
            )
    except Exception:
        pass

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
        src = hit.get("source") or "dense"
        block = (
            f"--- {path}:{start}-{end} (relevance {score:.3f}, {src}) ---\n"
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
