"""Capacity of the machine that actually runs inference.

The client laptop's GPU is irrelevant when Ollama/LM Studio runs on another box, so
capacity is resolved against the configured LLM endpoint:

* endpoint is local  -> probe this machine with nvidia-smi
* endpoint is remote -> use the operator-supplied `llmHostVramMb`, else report unknown

"Unknown" deliberately means "do not clamp anything". Silently shrinking the context
window because we could not measure a remote host is worse than leaving it alone.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

MB = 1024 * 1024

# num_ctx is resolved on every LLM turn, so probing the server each time would add
# seconds per turn - and far worse when the server is down and each probe waits for a
# connection failure. Results (including failures) are cached behind a short TTL.
_META_TTL_SEC = 300.0
_META_FAILURE_TTL_SEC = 30.0
_CAPACITY_TTL_SEC = 60.0

_meta_cache: Dict[Tuple[str, str], Tuple[float, Optional[Dict[str, Any]]]] = {}
_capacity_cache: Dict[str, Tuple[float, "InferenceCapacity"]] = {}
_cache_lock = threading.Lock()


def clear_capacity_caches() -> None:
    """Drop cached probes (settings changed, or a test wants a clean slate)."""
    with _cache_lock:
        _meta_cache.clear()
        _capacity_cache.clear()

# Bytes per weight by quantization level. Ollama reports e.g. "Q4_K_M", "Q8_0", "F16".
_QUANT_BYTES: Dict[str, float] = {
    "F32": 4.0,
    "F16": 2.0,
    "BF16": 2.0,
    "Q8_0": 1.0625,
    "Q6_K": 0.82,
    "Q5_K_M": 0.71,
    "Q5_K_S": 0.69,
    "Q5_0": 0.6875,
    "Q4_K_M": 0.5625,
    "Q4_K_S": 0.54,
    "Q4_0": 0.5625,
    "Q3_K_M": 0.43,
    "Q2_K": 0.33,
}
_DEFAULT_QUANT_BYTES = 0.5625  # assume Q4_K_M when the server does not say

# Bytes per KV cache element by cache type.
KV_CACHE_BYTES: Dict[str, float] = {
    "f16": 2.0,
    "q8_0": 1.0,
    "q4_0": 0.5,
}

# Leave room for the CUDA context, activations, and fragmentation.
VRAM_OVERHEAD_MB = 512
VRAM_SAFETY_FRACTION = 0.92

# Context floor: below this the agent cannot hold a system prompt plus one file.
MIN_USABLE_NUM_CTX = 4096

LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


@dataclass
class InferenceCapacity:
    """What we know about the host doing the generating."""

    vram_mb: Optional[int] = None
    ram_gb: Optional[float] = None
    host: str = ""
    is_local: bool = True
    source: str = "unknown"  # local_probe | manual_override | unknown

    @property
    def known(self) -> bool:
        return self.vram_mb is not None and self.vram_mb > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vramMb": self.vram_mb,
            "ramGb": self.ram_gb,
            "host": self.host,
            "isLocal": self.is_local,
            "source": self.source,
            "known": self.known,
        }


def endpoint_is_local(url: str) -> bool:
    """True when the LLM endpoint resolves to this machine."""
    raw = str(url or "").strip()
    if not raw:
        return True
    if "://" not in raw:
        raw = f"http://{raw}"
    host = (urlparse(raw).hostname or "").strip().lower()
    if not host:
        return True
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        pass
    try:
        if host == socket.gethostname().lower():
            return True
    except OSError:
        pass
    return False


def resolve_inference_capacity(settings: Optional[Dict[str, Any]] = None) -> InferenceCapacity:
    """Capacity of whichever machine serves the configured chat endpoint (cached)."""
    try:
        from backend.services.workflow_settings import get_workflow_settings

        ws = settings if settings is not None else get_workflow_settings()
    except Exception:
        ws = settings or {}

    base_url = str(ws.get("llmBaseUrl") or "").strip()
    cache_key = f"{base_url}|{ws.get('llmHostVramMb') or 0}"
    now = time.monotonic()
    with _cache_lock:
        hit = _capacity_cache.get(cache_key)
        if hit and now - hit[0] < _CAPACITY_TTL_SEC:
            return hit[1]

    capacity = _probe_inference_capacity(ws, base_url)
    with _cache_lock:
        _capacity_cache[cache_key] = (now, capacity)
    return capacity


def _probe_inference_capacity(ws: Dict[str, Any], base_url: str) -> InferenceCapacity:
    host = (urlparse(base_url if "://" in base_url else f"http://{base_url}").hostname or "") if base_url else ""
    is_local = endpoint_is_local(base_url)

    # An explicit override always wins; it is the only reliable signal for a remote host.
    try:
        override_mb = int(ws.get("llmHostVramMb") or 0)
    except (TypeError, ValueError):
        override_mb = 0
    if override_mb > 0:
        return InferenceCapacity(
            vram_mb=override_mb,
            host=host,
            is_local=is_local,
            source="manual_override",
        )

    if not is_local:
        # Never measure this laptop and pretend it describes the remote server.
        return InferenceCapacity(vram_mb=None, host=host, is_local=False, source="unknown")

    try:
        from backend.services.system_capacity import probe_system_capacity

        probe = probe_system_capacity()
    except Exception:
        return InferenceCapacity(host=host, is_local=True, source="unknown")

    vram = probe.get("vramMb")
    return InferenceCapacity(
        vram_mb=int(vram) if isinstance(vram, int) and vram > 0 else None,
        ram_gb=probe.get("ramGb"),
        host=host or "localhost",
        is_local=True,
        source="local_probe" if vram else "unknown",
    )


def _model_info_value(model_info: Dict[str, Any], suffix: str) -> Optional[int]:
    """Ollama prefixes model_info keys with the architecture (llama., qwen2., ...)."""
    for key, value in (model_info or {}).items():
        if key.endswith(suffix) and isinstance(value, (int, float)):
            return int(value)
    return None


def kv_bytes_per_token(model_meta: Dict[str, Any], *, kv_cache_type: str = "f16") -> Optional[int]:
    """KV cache cost of a single token, from /api/show metadata."""
    info = model_meta.get("model_info") or {}
    blocks = _model_info_value(info, ".block_count")
    kv_heads = _model_info_value(info, ".attention.head_count_kv")
    key_len = _model_info_value(info, ".attention.key_length")
    value_len = _model_info_value(info, ".attention.value_length")

    if not blocks or not kv_heads:
        return None
    if not key_len or not value_len:
        # Fall back to embedding_length / head_count when key/value length is absent.
        embed = _model_info_value(info, ".embedding_length")
        heads = _model_info_value(info, ".attention.head_count")
        if not embed or not heads:
            return None
        key_len = value_len = embed // heads

    per_element = KV_CACHE_BYTES.get(str(kv_cache_type).lower(), 2.0)
    return int(blocks * kv_heads * (key_len + value_len) * per_element)


def weights_bytes(model_meta: Dict[str, Any]) -> Optional[int]:
    """Approximate resident size of the model weights."""
    size = model_meta.get("size")
    if isinstance(size, (int, float)) and size > 0:
        return int(size)

    info = model_meta.get("model_info") or {}
    params = _model_info_value(info, "general.parameter_count")
    if not params:
        return None
    quant = str((model_meta.get("details") or {}).get("quantization_level") or "").upper()
    return int(params * _QUANT_BYTES.get(quant, _DEFAULT_QUANT_BYTES))


def fit_num_ctx(
    requested_num_ctx: int,
    *,
    vram_mb: Optional[int],
    model_meta: Optional[Dict[str, Any]] = None,
    kv_cache_type: str = "f16",
    min_num_ctx: int = MIN_USABLE_NUM_CTX,
) -> Dict[str, Any]:
    """Largest num_ctx that fits alongside the weights, capped at the request.

    Returns a dict with the chosen value plus the reasoning, so the UI and logs can
    explain *why* a context window was reduced instead of silently shrinking it.
    """
    requested = max(1024, int(requested_num_ctx or 0))
    result: Dict[str, Any] = {
        "numCtx": requested,
        "requested": requested,
        "clamped": False,
        "reason": "",
        "fitsInVram": None,
    }

    if not vram_mb or vram_mb <= 0 or not model_meta:
        result["reason"] = "capacity or model metadata unknown - left as requested"
        return result

    per_token = kv_bytes_per_token(model_meta, kv_cache_type=kv_cache_type)
    weights = weights_bytes(model_meta)
    if not per_token or not weights:
        result["reason"] = "model metadata incomplete - left as requested"
        return result

    budget = int(vram_mb * MB * VRAM_SAFETY_FRACTION) - (VRAM_OVERHEAD_MB * MB)
    kv_budget = budget - weights
    result["weightsMb"] = round(weights / MB)
    result["kvBytesPerToken"] = per_token
    result["kvBudgetMb"] = round(max(0, kv_budget) / MB)

    if kv_budget <= 0:
        # Even the weights do not fit; Ollama will offload layers to CPU.
        result["numCtx"] = min_num_ctx
        result["clamped"] = requested != min_num_ctx
        result["fitsInVram"] = False
        result["reason"] = (
            f"model weights (~{result['weightsMb']} MB) exceed usable VRAM "
            f"({vram_mb} MB) - expect CPU offload and slow generation"
        )
        return result

    max_ctx = int(kv_budget // per_token)
    if max_ctx >= requested:
        result["fitsInVram"] = True
        result["reason"] = "requested context fits in VRAM"
        return result

    chosen = max(min_num_ctx, (max_ctx // 1024) * 1024)
    result["numCtx"] = chosen
    result["clamped"] = True
    result["fitsInVram"] = chosen <= max_ctx
    result["reason"] = (
        f"clamped {requested} -> {chosen} to fit KV cache in {vram_mb} MB VRAM "
        f"(~{per_token} bytes/token, weights ~{result['weightsMb']} MB)"
    )
    return result


def fetch_model_meta(model: str, *, provider: Any = None) -> Optional[Dict[str, Any]]:
    """Model metadata from Ollama /api/show, cached. None when unavailable.

    Failures are cached too (briefly): an unreachable server must not cost a connection
    timeout on every single LLM turn.
    """
    name = str(model or "").strip()
    if not name:
        return None

    try:
        if provider is None:
            from backend.services.llm_provider import get_chat_provider

            provider = get_chat_provider()
    except Exception:
        return None

    key = (str(getattr(provider, "base_url", "") or ""), name)
    now = time.monotonic()
    with _cache_lock:
        hit = _meta_cache.get(key)
        if hit:
            age, cached = hit[0], hit[1]
            ttl = _META_TTL_SEC if cached else _META_FAILURE_TTL_SEC
            if now - age < ttl:
                return cached

    try:
        show = getattr(provider, "show_model", None)
        meta = show(name) if callable(show) else None
    except Exception:
        meta = None

    with _cache_lock:
        _meta_cache[key] = (now, meta)
    return meta
