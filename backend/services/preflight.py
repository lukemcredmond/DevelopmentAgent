"""Offline readiness preflight.

Answers one question: can this install do real work right now with no internet?
Every check reports ok / warn / fail plus a concrete fix, so a failure at 30,000 feet
is something you already knew about on the ground.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

# Ordered worst-first so the overall status is the first element present.
_SEVERITY = [STATUS_FAIL, STATUS_WARN, STATUS_OK]


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _overall(checks: List[Check]) -> str:
    present = {c.status for c in checks}
    for status in _SEVERITY:
        if status in present:
            return status
    return STATUS_OK


def _check_llm_endpoint(ws: Dict[str, Any]) -> tuple[Check, List[str]]:
    """Reachability of the chat endpoint, plus the model list for later checks."""
    from backend.services.llm_provider import get_chat_provider

    try:
        provider = get_chat_provider()
        health = provider.health()
    except Exception as exc:
        return (
            Check(
                "llm_endpoint",
                STATUS_FAIL,
                f"Could not build a provider: {exc}",
                "Check llmProvider / llmBaseUrl in Workflow settings.",
            ),
            [],
        )

    if not health.ok:
        return (
            Check(
                "llm_endpoint",
                STATUS_FAIL,
                f"{health.url} unreachable: {health.error}",
                "Start Ollama (`ollama serve`) or LM Studio on that host, and confirm "
                "the port is open to this machine.",
            ),
            [],
        )
    return (
        Check("llm_endpoint", STATUS_OK, f"{health.url} reachable, {len(health.models)} model(s)"),
        list(health.models),
    )


def _model_present(model: str, available: List[str]) -> bool:
    if not model:
        return False
    if model in available:
        return True
    # Ollama reports "name:tag"; accept a bare name when exactly one tag exists.
    return any(m.split(":")[0] == model.split(":")[0] for m in available)


def _check_role_models(available: List[str]) -> List[Check]:
    from backend import state

    checks: List[Check] = []
    missing: List[str] = []
    for role, model in (getattr(state, "PRIMARY_MODELS", None) or {}).items():
        if not model:
            continue
        if not _model_present(model, available):
            missing.append(f"{role}={model}")
    if missing:
        checks.append(
            Check(
                "role_models",
                STATUS_FAIL,
                f"Configured model(s) not on the server: {', '.join(missing)}",
                "Pull them on the inference host: " + "; ".join(
                    f"ollama pull {m.split('=', 1)[1]}" for m in missing
                ),
            )
        )
    else:
        checks.append(Check("role_models", STATUS_OK, "All role models present on the server"))
    return checks


def _check_embed_model(ws: Dict[str, Any], available: List[str]) -> Check:
    if not ws.get("enableSemanticSearch", True):
        return Check("embed_model", STATUS_OK, "Semantic search disabled; embeddings not needed")
    model = str(ws.get("embedModel") or "").strip()
    if not model:
        return Check("embed_model", STATUS_WARN, "No embedModel configured", "Set embedModel.")
    if _model_present(model, available):
        return Check("embed_model", STATUS_OK, f"{model} present")
    return Check(
        "embed_model",
        STATUS_WARN,
        f"Embedding model '{model}' not on the server; semantic search will degrade",
        f"ollama pull {model}",
    )


def _check_vector_store(ws: Dict[str, Any]) -> Check:
    if not ws.get("enableSemanticSearch", True):
        return Check("qdrant", STATUS_OK, "Semantic search disabled")
    url = str(ws.get("qdrantUrl") or "").strip()
    if not url:
        return Check("qdrant", STATUS_WARN, "No qdrantUrl set; semantic search unavailable")
    try:
        import requests

        resp = requests.get(f"{url.rstrip('/')}/healthz", timeout=3)
        if resp.status_code == 200:
            return Check("qdrant", STATUS_OK, f"{url} reachable")
        return Check("qdrant", STATUS_WARN, f"{url} returned HTTP {resp.status_code}")
    except Exception as exc:
        # Degraded, not fatal: the agent still works without semantic retrieval.
        return Check(
            "qdrant",
            STATUS_WARN,
            f"{url} unreachable ({type(exc).__name__}); semantic search will be skipped",
            "Start Qdrant, or set enableSemanticSearch=false to silence this.",
        )


def _check_capacity(ws: Dict[str, Any]) -> Check:
    from backend.services.llm_capacity import resolve_inference_capacity

    capacity = resolve_inference_capacity(ws)
    if capacity.known:
        return Check(
            "inference_capacity",
            STATUS_OK,
            f"{capacity.vram_mb} MB VRAM ({capacity.source})",
        )
    if not capacity.is_local:
        return Check(
            "inference_capacity",
            STATUS_WARN,
            f"VRAM of remote host '{capacity.host or 'unknown'}' is unknown, so context "
            "cannot be sized to fit",
            "Set llmHostVramMb to that machine's VRAM in MB to enable context fitting "
            "and automatic single-model mode.",
        )
    return Check(
        "inference_capacity",
        STATUS_WARN,
        "Local GPU could not be probed (nvidia-smi missing?)",
        "Set llmHostVramMb manually to enable context fitting.",
    )


def _check_kv_cache_hint(ws: Dict[str, Any]) -> Check:
    """We cannot read the server's env, so this is advice rather than a measurement."""
    kv = str(ws.get("ollamaKvCacheType") or "f16").lower()
    if kv == "f16":
        return Check(
            "kv_cache",
            STATUS_WARN,
            "KV cache assumed f16, which doubles context memory versus q8_0",
            "On the Ollama host set OLLAMA_KV_CACHE_TYPE=q8_0 (and OLLAMA_FLASH_ATTENTION=1), "
            "then set ollamaKvCacheType=q8_0 here so the fit calculation matches.",
        )
    return Check(
        "kv_cache",
        STATUS_OK,
        f"Context fitting assumes KV cache '{kv}' — must match OLLAMA_KV_CACHE_TYPE on the host",
    )


def _is_public_url(url: str) -> bool:
    """A URL that needs the internet (as opposed to a LAN or loopback address)."""
    if not url.startswith("http"):
        return False
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    # LAN hosts stay reachable with no internet, which is the whole point offline.
    return not (
        host.startswith("192.168.")
        or host.startswith("10.")
        or host.endswith(".local")
        or any(host.startswith(f"172.{n}.") for n in range(16, 32))
    )


def _check_outbound_features(ws: Dict[str, Any]) -> List[Check]:
    """Flag anything enabled that needs the public internet."""
    checks: List[Check] = []
    online: List[str] = []
    if ws.get("enableWebSearch"):
        online.append("web search")
    if ws.get("phoneNotifyEnabled"):
        online.append("Discord phone notifications")
    if ws.get("discordBotEnabled"):
        online.append("Discord control bot")
    servers = ws.get("mcpServers")
    for server in servers if isinstance(servers, list) else []:
        if isinstance(server, dict):
            url = str(server.get("url") or "")
            if _is_public_url(url):
                online.append(f"MCP server {server.get('name') or url}")

    tools = ws.get("customTools")
    for tool in tools if isinstance(tools, list) else []:
        if isinstance(tool, dict) and str(tool.get("executor") or "") == "http":
            url = str((tool.get("http") or {}).get("url") if isinstance(tool.get("http"), dict) else tool.get("url") or "")
            if _is_public_url(url):
                online.append(f"custom HTTP tool {tool.get('name') or url}")

    if online:
        checks.append(
            Check(
                "offline_safety",
                STATUS_WARN,
                "Enabled features that need network access: " + ", ".join(online),
                "These degrade gracefully when offline, but disable them to avoid the "
                "per-call timeout cost.",
            )
        )
    else:
        checks.append(Check("offline_safety", STATUS_OK, "No internet-dependent features enabled"))
    return checks


def _check_workspace() -> Check:
    import os

    from backend import state

    path = state.WORKSPACE_DIR
    if path and os.path.isdir(path):
        return Check("workspace", STATUS_OK, f"{path} exists")
    return Check("workspace", STATUS_FAIL, f"Workspace directory missing: {path}", "Create it or pick another in Project Config.")


def run_preflight(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run every readiness check. Never raises: a broken check is itself a finding."""
    try:
        from backend.services.workflow_settings import get_workflow_settings

        ws = settings if settings is not None else get_workflow_settings()
    except Exception:
        ws = settings or {}

    def safely(name: str, fn) -> List[Check]:
        """A crashing check is reported as a finding, never propagated."""
        try:
            out = fn()
        except Exception as exc:
            return [Check(name, STATUS_WARN, f"Check failed to run: {type(exc).__name__}: {exc}")]
        return out if isinstance(out, list) else [out]

    checks: List[Check] = []
    try:
        endpoint_check, available = _check_llm_endpoint(ws)
    except Exception as exc:
        endpoint_check, available = Check("llm_endpoint", STATUS_FAIL, f"Check failed: {exc}"), []
    checks.append(endpoint_check)

    if endpoint_check.status == STATUS_OK:
        checks.extend(safely("role_models", lambda: _check_role_models(available)))
        checks.extend(safely("embed_model", lambda: _check_embed_model(ws, available)))
    else:
        checks.append(
            Check(
                "role_models",
                STATUS_FAIL,
                "Skipped - the LLM endpoint is unreachable",
                "Fix llm_endpoint first.",
            )
        )

    checks.extend(safely("inference_capacity", lambda: _check_capacity(ws)))
    checks.extend(safely("kv_cache", lambda: _check_kv_cache_hint(ws)))
    checks.extend(safely("qdrant", lambda: _check_vector_store(ws)))
    checks.extend(safely("offline_safety", lambda: _check_outbound_features(ws)))
    checks.extend(safely("workspace", _check_workspace))

    status = _overall(checks)
    return {
        "status": status,
        "ready": status != STATUS_FAIL,
        "checks": [c.to_dict() for c in checks],
        "summary": summarize(checks),
    }


def summarize(checks: List[Check]) -> str:
    fails = [c for c in checks if c.status == STATUS_FAIL]
    warns = [c for c in checks if c.status == STATUS_WARN]
    if fails:
        return f"{len(fails)} blocking issue(s): " + "; ".join(c.name for c in fails)
    if warns:
        return f"Ready, with {len(warns)} warning(s): " + "; ".join(c.name for c in warns)
    return "Ready for offline work"


def log_preflight_on_startup() -> Dict[str, Any]:
    """Run preflight at boot and write the findings to the system log."""
    from backend.services.logs import add_system_log

    result = run_preflight()
    level = {STATUS_OK: "info", STATUS_WARN: "warning", STATUS_FAIL: "error"}[result["status"]]
    add_system_log("System", level, f"Preflight: {result['summary']}")
    for check in result["checks"]:
        if check["status"] == STATUS_OK:
            continue
        message = f"Preflight [{check['name']}] {check['detail']}"
        if check.get("fix"):
            message += f" -> {check['fix']}"
        add_system_log("System", "warning" if check["status"] == STATUS_WARN else "error", message)
    return result
