"""Pluggable LLM chat/embed/health providers (Ollama + OpenAI-compatible)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

import requests

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"

# Most sprint/chat payloads still default `ollama_url` to these values, so they cannot
# be treated as a deliberate per-request override of a configured provider.
LEGACY_OLLAMA_URLS = {"http://localhost:11434", "http://127.0.0.1:11434"}

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI_COMPAT = "openai_compat"
PRESET_OLLAMA = "ollama"
PRESET_LMSTUDIO = "lmstudio"
PRESET_CUSTOM = "custom"

PRESET_DEFAULTS: Dict[str, Dict[str, str]] = {
    PRESET_OLLAMA: {"llmProvider": PROVIDER_OLLAMA, "llmBaseUrl": DEFAULT_OLLAMA_URL},
    PRESET_LMSTUDIO: {"llmProvider": PROVIDER_OPENAI_COMPAT, "llmBaseUrl": DEFAULT_LMSTUDIO_URL},
    PRESET_CUSTOM: {"llmProvider": PROVIDER_OPENAI_COMPAT, "llmBaseUrl": DEFAULT_LMSTUDIO_URL},
}


@dataclass(frozen=True)
class ProviderCapabilities:
    num_ctx: bool = False
    keep_alive: bool = False
    vram_unload: bool = False
    native_tool_name: bool = False


@dataclass
class ToolFunction:
    name: str
    arguments: Any = field(default_factory=dict)


@dataclass
class ProviderToolCall:
    id: str
    function: ToolFunction


@dataclass
class ProviderMessage:
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[ProviderToolCall]] = None


@dataclass
class ChatResult:
    message: ProviderMessage
    prompt_eval_count: int = 0
    eval_count: int = 0
    raw: Any = None


@dataclass
class HealthResult:
    ok: bool
    url: str
    models: List[str] = field(default_factory=list)
    error: Optional[str] = None
    provider: str = PROVIDER_OLLAMA


DEFAULT_HEALTH_TIMEOUT_SEC = 5.0
MIN_LMSTUDIO_LOAD_CONTEXT = 4096


class LlmProvider:
    provider_id: str = PROVIDER_OLLAMA
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __init__(self, base_url: str, *, api_key: str = "", timeout_sec: float = 300.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_sec = float(timeout_sec or 300.0)
        # Raised by callers that probe while the server may be busy loading a model.
        self.health_timeout_sec = DEFAULT_HEALTH_TIMEOUT_SEC

    def list_models(self) -> List[str]:
        raise NotImplementedError

    def health(self) -> HealthResult:
        raise NotImplementedError

    def chat(
        self,
        model: str,
        messages: Sequence[Any],
        *,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        stream: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> Union[ChatResult, Iterator[ChatResult]]:
        raise NotImplementedError

    def embed(self, model: str, text: str) -> Optional[List[float]]:
        raise NotImplementedError

    def unload(self, model: str) -> bool:
        return False

    def warm(self, model: str, *, keep_alive: Optional[str] = None) -> bool:
        return False

    def unload_loaded_except(self, keep_model: str) -> Dict[str, Any]:
        """Best-effort: free VRAM held by models other than keep_model.

        Test path only. Never raises; reports why nothing happened so the
        caller can tell "nothing to unload" apart from "could not unload".
        """
        return {"status": "unsupported", "unloaded": []}

    def load_model_for_test(self, model: str, *, context_length: int = 4096) -> Dict[str, Any]:
        """Load a model with an explicit small context. Test path only.

        Avoids relying on the server's just-in-time defaults, which may reserve
        a full-size KV cache and trip a memory guardrail.
        """
        return {"status": "unsupported"}

    def show_model(self, model: str) -> Optional[Dict[str, Any]]:
        """Architecture/quantization metadata used to size the KV cache.

        None when the server cannot describe the model; callers must treat that as
        "unknown" rather than assuming a default.
        """
        return None


class OllamaProvider(LlmProvider):
    provider_id = PROVIDER_OLLAMA
    capabilities = ProviderCapabilities(
        num_ctx=True,
        keep_alive=True,
        vram_unload=True,
        native_tool_name=True,
    )

    def __init__(self, base_url: str, *, api_key: str = "", timeout_sec: float = 300.0):
        super().__init__(strip_openai_suffix(base_url) or DEFAULT_OLLAMA_URL, api_key=api_key, timeout_sec=timeout_sec)
        self._client = None
        self._client_timeout: Optional[float] = None

    def _get_client(self):
        from ollama import Client

        if self._client is None or self._client_timeout != self.timeout_sec:
            self._client = Client(host=self.base_url, timeout=self.timeout_sec)
            self._client_timeout = self.timeout_sec
        return self._client

    def list_models(self) -> List[str]:
        result = self.health()
        return result.models

    def show_model(self, model: str) -> Optional[Dict[str, Any]]:
        # Metadata is an optimization, never worth stalling an agent turn for.
        try:
            response = requests.post(
                f"{self.base_url}/api/show",
                json={"model": model},
                timeout=min(3.0, self.health_timeout_sec),
            )
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
        except (requests.RequestException, ValueError):
            return None

    def health(self) -> HealthResult:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=self.health_timeout_sec)
            if response.status_code == 200:
                models = [m.get("name") for m in response.json().get("models", []) if m.get("name")]
                return HealthResult(ok=True, url=self.base_url, models=models, provider=self.provider_id)
            return HealthResult(
                ok=False,
                url=self.base_url,
                error=f"HTTP {response.status_code}",
                provider=self.provider_id,
            )
        except requests.RequestException as exc:
            return HealthResult(ok=False, url=self.base_url, error=str(exc), provider=self.provider_id)

    def chat(
        self,
        model: str,
        messages: Sequence[Any],
        *,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        stream: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> Union[ChatResult, Iterator[ChatResult]]:
        opts = dict(options or {})
        keep_alive = opts.pop("keep_alive", None)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "tools": tools,
            "stream": stream,
            "options": opts,
        }
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        result = self._get_client().chat(**kwargs)
        if stream:
            return _iter_ollama_stream(result)
        return chat_result_from_ollama(result)

    def embed(self, model: str, text: str) -> Optional[List[float]]:
        try:
            response = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=min(60.0, max(10.0, self.timeout_sec)),
            )
            if response.status_code == 200:
                embedding = response.json().get("embedding")
                if isinstance(embedding, list) and embedding:
                    return embedding
        except requests.RequestException:
            pass
        return None

    def unload(self, model: str) -> bool:
        try:
            self._get_client().chat(
                model=model,
                messages=[{"role": "user", "content": "."}],
                options={"num_predict": 1},
                keep_alive=0,
            )
            return True
        except Exception:
            return False

    def unload_loaded_except(self, keep_model: str) -> Dict[str, Any]:
        keep = (keep_model or "").strip()
        try:
            response = requests.get(
                f"{self.base_url}/api/ps", timeout=min(10.0, max(2.0, self.health_timeout_sec))
            )
            if response.status_code != 200:
                return {
                    "status": "error",
                    "unloaded": [],
                    "detail": f"/api/ps returned HTTP {response.status_code}",
                }
            models = response.json().get("models") or []
        except Exception as exc:
            return {"status": "error", "unloaded": [], "detail": f"{type(exc).__name__}: {exc}"}

        unloaded: List[str] = []
        failed: List[str] = []
        for item in models:
            name = str((item or {}).get("name") or (item or {}).get("model") or "").strip()
            if not name or _same_model_id(name, keep):
                continue
            if self.unload(name):
                unloaded.append(name)
            else:
                failed.append(name)
        if failed:
            return {
                "status": "error",
                "unloaded": unloaded,
                "detail": f"could not unload {', '.join(failed)}",
            }
        return {"status": "unloaded" if unloaded else "none", "unloaded": unloaded}

    def warm(self, model: str, *, keep_alive: Optional[str] = None) -> bool:
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "."}],
                "options": {"num_predict": 1},
            }
            if keep_alive:
                kwargs["keep_alive"] = str(keep_alive)
            self._get_client().chat(**kwargs)
            return True
        except Exception:
            return False


class OpenAICompatProvider(LlmProvider):
    provider_id = PROVIDER_OPENAI_COMPAT
    capabilities = ProviderCapabilities(
        num_ctx=False,
        keep_alive=False,
        vram_unload=False,
        native_tool_name=False,
    )

    def __init__(self, base_url: str, *, api_key: str = "", timeout_sec: float = 300.0):
        super().__init__(ensure_openai_base(base_url), api_key=api_key or "lm-studio", timeout_sec=timeout_sec)
        self._loaded_model: Optional[str] = None
        self._loaded_context: Optional[int] = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def list_models(self) -> List[str]:
        return self.health().models

    def health(self) -> HealthResult:
        try:
            response = requests.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=self.health_timeout_sec
            )
            if response.status_code == 200:
                data = response.json()
                models = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
                return HealthResult(ok=True, url=self.base_url, models=models, provider=self.provider_id)
            return HealthResult(
                ok=False,
                url=self.base_url,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
                provider=self.provider_id,
            )
        except requests.RequestException as exc:
            return HealthResult(ok=False, url=self.base_url, error=str(exc), provider=self.provider_id)

    def chat(
        self,
        model: str,
        messages: Sequence[Any],
        *,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        stream: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> Union[ChatResult, Iterator[ChatResult]]:
        opts = dict(options or {})
        self.ensure_model_loaded(model, context_length=opts.get("num_ctx"))
        payload: Dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages),
            "stream": stream,
            "temperature": opts.get("temperature", 0.1),
        }
        if tools:
            payload["tools"] = list(tools)
        if opts.get("num_predict") is not None:
            payload["max_tokens"] = int(opts["num_predict"])
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_sec,
            stream=stream,
        )
        if stream:
            response.raise_for_status()
            return _iter_openai_stream(response)
        if response.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {response.status_code}: {response.text[:500]}")
        return chat_result_from_openai(response.json())

    def embed(self, model: str, text: str) -> Optional[List[float]]:
        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": model, "input": text[:4000]},
                timeout=min(60.0, max(10.0, self.timeout_sec)),
            )
            if response.status_code == 200:
                data = response.json().get("data") or []
                if data and isinstance(data[0], dict):
                    embedding = data[0].get("embedding")
                    if isinstance(embedding, list) and embedding:
                        return embedding
        except requests.RequestException:
            pass
        return None

    def _native_timeout(self) -> float:
        return min(30.0, max(5.0, self.health_timeout_sec))

    def _native_models_payload(self) -> Dict[str, Any]:
        host = strip_openai_suffix(self.base_url)
        timeout = self._native_timeout()
        try:
            response = requests.get(
                f"{host}/api/v1/models", headers=self._headers(), timeout=timeout
            )
        except Exception as exc:
            return {"status": "error", "models": [], "detail": f"{type(exc).__name__}: {exc}"}
        if response.status_code == 404:
            return {
                "status": "unavailable",
                "models": [],
                "detail": "native /api/v1 model API not found (needs LM Studio 0.4.0+)",
            }
        if response.status_code != 200:
            return {
                "status": "error",
                "models": [],
                "detail": f"/api/v1/models returned HTTP {response.status_code}",
            }
        payload = response.json() or {}
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return {"status": "error", "models": [], "detail": "unexpected /api/v1/models body"}
        return {"status": "ok", "models": models}

    def unload_loaded_except(self, keep_model: str) -> Dict[str, Any]:
        """Unload every LM Studio instance except keep_model via native /api/v1.

        The /api/v1 routes require LM Studio 0.4.0+; on older builds this
        reports "unavailable" rather than silently leaving VRAM occupied.
        """
        keep = (keep_model or "").strip()
        listed = self._native_models_payload()
        if listed.get("status") != "ok":
            return {
                "status": listed.get("status") or "error",
                "unloaded": [],
                "detail": listed.get("detail") or "could not list models",
            }

        host = strip_openai_suffix(self.base_url)
        timeout = self._native_timeout()
        unloaded: List[str] = []
        failures: List[str] = []
        for item in listed.get("models") or []:
            if not isinstance(item, dict):
                continue
            instances = item.get("loaded_instances") or []
            if not isinstance(instances, list):
                continue
            for instance in instances:
                instance_id = ""
                if isinstance(instance, dict):
                    instance_id = str(instance.get("id") or "").strip()
                elif isinstance(instance, str):
                    instance_id = instance.strip()
                if not instance_id or _same_model_id(instance_id, keep):
                    continue
                try:
                    unload_response = requests.post(
                        f"{host}/api/v1/models/unload",
                        headers=self._headers(),
                        json={"instance_id": instance_id},
                        timeout=timeout,
                    )
                    if unload_response.status_code < 400:
                        unloaded.append(instance_id)
                    else:
                        failures.append(
                            f"{instance_id} (HTTP {unload_response.status_code})"
                        )
                except Exception as exc:
                    failures.append(f"{instance_id} ({type(exc).__name__})")
        if failures:
            return {
                "status": "error",
                "unloaded": unloaded,
                "detail": f"could not unload {', '.join(failures)}",
            }
        return {"status": "unloaded" if unloaded else "none", "unloaded": unloaded}

    def load_model_for_test(self, model: str, *, context_length: int = 4096) -> Dict[str, Any]:
        return self._native_load(model, context_length=context_length)

    def _native_load(self, model: str, *, context_length: int) -> Dict[str, Any]:
        host = strip_openai_suffix(self.base_url)
        try:
            response = requests.post(
                f"{host}/api/v1/models/load",
                headers=self._headers(),
                json={
                    "model": model,
                    "context_length": int(context_length),
                    "echo_load_config": True,
                },
                timeout=self.timeout_sec,
            )
        except Exception as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        if response.status_code == 404:
            return {
                "status": "unavailable",
                "detail": "native /api/v1 model API not found (needs LM Studio 0.4.0+)",
            }
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"HTTP {response.status_code}: {response.text[:300]}",
            }
        try:
            body = response.json() or {}
        except Exception:
            body = {}
        return {
            "status": "loaded",
            "config": body.get("load_config") or {},
            "loadTimeSeconds": body.get("load_time_seconds"),
        }

    def ensure_model_loaded(
        self, model: str, *, context_length: Optional[int] = None
    ) -> Dict[str, Any]:
        """Load `model` via native /api/v1 before chat/completions.

        JIT chat/completions often reserves a full-size KV cache and is refused
        by LM Studio's memory guardrail, so sprint never shows a loaded model.
        Missing native API falls through to JIT chat.
        """
        wanted = _lmstudio_load_context(context_length)
        if (
            self._loaded_model
            and _same_model_id(self._loaded_model, model)
            and (self._loaded_context is None or self._loaded_context >= wanted)
        ):
            _provider_log(
                f"LM Studio already has {model} loaded (context={self._loaded_context or wanted}) at {self.base_url}"
            )
            return {
                "status": "already",
                "model": model,
                "contextLength": self._loaded_context or wanted,
            }

        listed = self._native_models_payload()
        if listed.get("status") == "unavailable":
            _provider_log(
                f"LM Studio native load API unavailable at {self.base_url} — using just-in-time chat",
                "warning",
            )
            return listed

        loaded_ctx = _loaded_context_for_model(model, listed.get("models") or [])
        if loaded_ctx is not None and (loaded_ctx == 0 or loaded_ctx >= wanted):
            self._loaded_model = model
            self._loaded_context = loaded_ctx or wanted
            _provider_log(
                f"LM Studio already has {model} loaded (context={self._loaded_context}) at {self.base_url}"
            )
            return {
                "status": "already",
                "model": model,
                "contextLength": self._loaded_context,
            }

        self.unload_loaded_except(model)
        ctx = wanted
        last: Dict[str, Any] = {}
        while ctx >= MIN_LMSTUDIO_LOAD_CONTEXT:
            _provider_log(f"Loading {model} in LM Studio (context={ctx}) at {self.base_url}")
            last = self._native_load(model, context_length=ctx)
            status = last.get("status")
            if status == "loaded":
                actual = (last.get("config") or {}).get("context_length") or ctx
                try:
                    actual_int = int(actual)
                except (TypeError, ValueError):
                    actual_int = ctx
                self._loaded_model = model
                self._loaded_context = actual_int
                return last
            if status == "unavailable":
                _provider_log(
                    f"LM Studio native load API unavailable at {self.base_url} — using just-in-time chat",
                    "warning",
                )
                return last
            nxt = max(MIN_LMSTUDIO_LOAD_CONTEXT, ctx // 2)
            if nxt >= ctx:
                break
            _provider_log(
                f"LM Studio refused {model} at context={ctx} ({last.get('error') or 'unknown'}) — retrying at {nxt}",
                "warning",
            )
            ctx = nxt
        _provider_log(
            f"LM Studio failed to load {model} at {self.base_url}: {last.get('error') or last.get('detail') or 'unknown'} — falling back to chat",
            "warning",
        )
        return last or {"status": "error", "error": "load failed"}


def _provider_log(message: str, level: str = "info") -> None:
    try:
        from backend.services.logs import add_system_log

        add_system_log("System", level, message)
    except Exception:
        pass


def _lmstudio_load_context(requested: Optional[int]) -> int:
    if requested is not None:
        try:
            return max(MIN_LMSTUDIO_LOAD_CONTEXT, int(requested))
        except (TypeError, ValueError):
            pass
    try:
        from backend.services.prompt_budget import resolve_ollama_num_ctx

        return max(MIN_LMSTUDIO_LOAD_CONTEXT, int(resolve_ollama_num_ctx()))
    except Exception:
        return MIN_LMSTUDIO_LOAD_CONTEXT


def _instance_id(instance: Any) -> str:
    if isinstance(instance, dict):
        return str(instance.get("id") or "").strip()
    if isinstance(instance, str):
        return instance.strip()
    return ""


def _instance_context_length(instance: Any) -> Optional[int]:
    if not isinstance(instance, dict):
        return None
    cfg = instance.get("config") or instance.get("load_config") or {}
    raw = None
    if isinstance(cfg, dict):
        raw = cfg.get("context_length")
    if raw is None:
        raw = instance.get("context_length")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _loaded_context_for_model(model: str, native_models: Sequence[Any]) -> Optional[int]:
    """Context length if `model` is loaded; 0 if loaded with unknown size; None if not loaded."""
    found = False
    best: Optional[int] = None
    for item in native_models:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("id") or item.get("model") or "").strip()
        instances = item.get("loaded_instances") or []
        if not isinstance(instances, list) or not instances:
            continue
        matched = _same_model_id(key, model)
        if not matched:
            matched = any(_same_model_id(_instance_id(inst), model) for inst in instances)
        if not matched:
            continue
        found = True
        for inst in instances:
            ctx = _instance_context_length(inst)
            if ctx is not None:
                best = ctx if best is None else max(best, ctx)
    if not found:
        return None
    return best if best is not None else 0


def strip_openai_suffix(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if raw.lower().endswith("/v1"):
        return raw[:-3].rstrip("/")
    return raw


def _same_model_id(left: str, right: str) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def ensure_openai_base(url: str) -> str:
    raw = (url or "").strip().rstrip("/") or DEFAULT_LMSTUDIO_URL
    if raw.lower().endswith("/v1"):
        return raw
    return f"{raw}/v1"


def apply_llm_preset(preset: str) -> Dict[str, str]:
    key = str(preset or PRESET_OLLAMA).strip().lower()
    return dict(PRESET_DEFAULTS.get(key) or PRESET_DEFAULTS[PRESET_OLLAMA])


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def is_legacy_ollama_default(url: str) -> bool:
    """True when a caller passed the historic hard-coded Ollama URL default."""
    return _normalize_url(url) in LEGACY_OLLAMA_URLS


def infer_provider_from_url(url: str, fallback: str = PROVIDER_OLLAMA) -> str:
    raw = _normalize_url(url)
    if "/v1" in raw:
        return PROVIDER_OPENAI_COMPAT
    if ":11434" in raw:
        return PROVIDER_OLLAMA
    if ":1234" in raw:
        return PROVIDER_OPENAI_COMPAT
    return fallback or PROVIDER_OLLAMA


def normalize_llm_provider_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep preset/provider aligned with llmBaseUrl so LM Studio cannot look like Ollama."""
    if not isinstance(data, dict):
        return data
    url = str(data.get("llmBaseUrl") or "").strip()
    preset = str(data.get("llmProviderPreset") or "").strip().lower()
    provider = str(data.get("llmProvider") or "").strip().lower()
    inferred = infer_provider_from_url(url, fallback=provider or PROVIDER_OLLAMA)
    looks_lmstudio = ":1234" in url.lower()
    looks_openai = inferred == PROVIDER_OPENAI_COMPAT
    if looks_openai and preset in ("", PRESET_OLLAMA):
        data["llmProvider"] = PROVIDER_OPENAI_COMPAT
        data["llmProviderPreset"] = PRESET_LMSTUDIO if looks_lmstudio else PRESET_CUSTOM
        if not url:
            data["llmBaseUrl"] = DEFAULT_LMSTUDIO_URL
    elif preset == PRESET_LMSTUDIO:
        data["llmProvider"] = PROVIDER_OPENAI_COMPAT
        if not url:
            data["llmBaseUrl"] = DEFAULT_LMSTUDIO_URL
    elif preset == PRESET_CUSTOM:
        data["llmProvider"] = PROVIDER_OPENAI_COMPAT
    elif preset == PRESET_OLLAMA or not preset:
        if inferred == PROVIDER_OLLAMA:
            data["llmProvider"] = PROVIDER_OLLAMA
            data["llmProviderPreset"] = PRESET_OLLAMA
    return data


def _settings() -> Dict[str, Any]:
    from backend.services.workflow_settings import get_workflow_settings

    return get_workflow_settings()


def _timeout_from_settings(ws: Optional[Dict[str, Any]] = None) -> float:
    data = ws if ws is not None else _settings()
    return float(data.get("ollamaRequestTimeoutSec") or 300)


def chat_config(*, override_url: Optional[str] = None, ws: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = ws if ws is not None else _settings()
    preset = str(data.get("llmProviderPreset") or PRESET_OLLAMA).strip().lower()
    provider = str(data.get("llmProvider") or PROVIDER_OLLAMA).strip().lower()
    url = str(data.get("llmBaseUrl") or "").strip()
    if preset == PRESET_LMSTUDIO:
        provider = PROVIDER_OPENAI_COMPAT
        url = url or DEFAULT_LMSTUDIO_URL
    elif preset == PRESET_CUSTOM:
        provider = PROVIDER_OPENAI_COMPAT
    override = str(override_url or "").strip()
    # Ignore the legacy Ollama default so it cannot silently send an OpenAI-compatible
    # setup back to port 11434 while the UI still polls the configured server.
    if override and not (provider == PROVIDER_OPENAI_COMPAT and is_legacy_ollama_default(override)):
        url = override
        provider = infer_provider_from_url(url, fallback=provider)
    api_key = str(data.get("llmApiKey") or "").strip()
    return {
        "provider": provider if provider in (PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPAT) else PROVIDER_OLLAMA,
        "baseUrl": url or DEFAULT_OLLAMA_URL,
        "apiKey": api_key,
        "timeoutSec": _timeout_from_settings(data),
        "preset": preset,
    }


def embed_config(*, override_url: Optional[str] = None, ws: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = ws if ws is not None else _settings()
    mode = str(data.get("embedProvider") or PROVIDER_OLLAMA).strip().lower()
    if mode == "inherit":
        cfg = chat_config(override_url=override_url, ws=data)
        return cfg
    url = str(data.get("embedBaseUrl") or DEFAULT_OLLAMA_URL).strip()
    if mode == PROVIDER_OPENAI_COMPAT:
        return {
            "provider": PROVIDER_OPENAI_COMPAT,
            "baseUrl": url or DEFAULT_LMSTUDIO_URL,
            "apiKey": str(data.get("llmApiKey") or "").strip(),
            "timeoutSec": _timeout_from_settings(data),
            "preset": data.get("llmProviderPreset") or PRESET_OLLAMA,
        }
    return {
        "provider": PROVIDER_OLLAMA,
        "baseUrl": strip_openai_suffix(url) or DEFAULT_OLLAMA_URL,
        "apiKey": "",
        "timeoutSec": _timeout_from_settings(data),
        "preset": PRESET_OLLAMA,
    }


def build_provider(cfg: Dict[str, Any]) -> LlmProvider:
    provider = str(cfg.get("provider") or PROVIDER_OLLAMA)
    url = str(cfg.get("baseUrl") or DEFAULT_OLLAMA_URL)
    key = str(cfg.get("apiKey") or "")
    timeout = float(cfg.get("timeoutSec") or 300)
    if provider == PROVIDER_OPENAI_COMPAT:
        return OpenAICompatProvider(url, api_key=key, timeout_sec=timeout)
    return OllamaProvider(url, api_key=key, timeout_sec=timeout)


def get_chat_provider(*, override_url: Optional[str] = None) -> LlmProvider:
    return build_provider(chat_config(override_url=override_url))


def resolve_chat_base_url(override_url: Optional[str] = None) -> str:
    """URL Plan/Sprint should call — workflow llmBaseUrl, not a stale 11434 override."""
    return str(chat_config(override_url=override_url).get("baseUrl") or DEFAULT_OLLAMA_URL)


def get_embed_provider(*, override_url: Optional[str] = None) -> LlmProvider:
    return build_provider(embed_config(override_url=override_url))


def message_as_dict(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    from backend.services.llm_tool_recovery import assistant_message_to_chat_dict

    return assistant_message_to_chat_dict(message)


def to_openai_messages(messages: Sequence[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in messages:
        item = message_as_dict(raw)
        role = str(item.get("role") or "user")
        if role == "tool":
            converted: Dict[str, Any] = {"role": "tool", "content": item.get("content") or ""}
            if item.get("tool_call_id"):
                converted["tool_call_id"] = str(item["tool_call_id"])
            elif item.get("tool_name"):
                converted["name"] = str(item["tool_name"])
            out.append(converted)
            continue
        if role == "assistant" and item.get("tool_calls"):
            tool_calls = []
            for index, tc in enumerate(item.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls.append(
                    {
                        "id": str(tc.get("id") or f"call_{index}"),
                        "type": "function",
                        "function": {
                            "name": fn.get("name"),
                            "arguments": args or "{}",
                        },
                    }
                )
            out.append(
                {
                    "role": "assistant",
                    "content": item.get("content") or None,
                    "tool_calls": tool_calls,
                }
            )
            continue
        out.append({"role": role, "content": item.get("content") or ""})
    return out


def chat_result_from_ollama(result: Any) -> ChatResult:
    from backend.services.agent_usage import extract_ollama_token_counts

    prompt, eval_tokens, _total, _reported = extract_ollama_token_counts(result)
    msg = getattr(result, "message", None)
    tool_calls: List[ProviderToolCall] = []
    raw_calls = getattr(msg, "tool_calls", None) if msg is not None else None
    for index, tc in enumerate(raw_calls or []):
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", None) if fn is not None else None
        args = getattr(fn, "arguments", None) if fn is not None else {}
        call_id = str(getattr(tc, "id", None) or f"call_{index}")
        if name:
            tool_calls.append(ProviderToolCall(id=call_id, function=ToolFunction(name=str(name), arguments=args or {})))
    return ChatResult(
        message=ProviderMessage(
            role=str(getattr(msg, "role", None) or "assistant"),
            content=getattr(msg, "content", None) if msg is not None else None,
            tool_calls=tool_calls or None,
        ),
        prompt_eval_count=prompt,
        eval_count=eval_tokens,
        raw=result,
    )


def chat_result_from_openai(payload: Dict[str, Any]) -> ChatResult:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    tool_calls: List[ProviderToolCall] = []
    for index, tc in enumerate(message.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        try:
            parsed = json.loads(args) if isinstance(args, str) else args
        except json.JSONDecodeError:
            parsed = args
        name = fn.get("name")
        if not name:
            continue
        tool_calls.append(
            ProviderToolCall(
                id=str(tc.get("id") or f"call_{index}"),
                function=ToolFunction(name=str(name), arguments=parsed if parsed is not None else {}),
            )
        )
    return ChatResult(
        message=ProviderMessage(
            role=str(message.get("role") or "assistant"),
            content=message.get("content"),
            tool_calls=tool_calls or None,
        ),
        prompt_eval_count=prompt,
        eval_count=completion,
        raw=payload,
    )


def _iter_ollama_stream(result: Any) -> Iterator[ChatResult]:
    for chunk in result:
        yield chat_result_from_ollama(chunk)


def _iter_openai_stream(response: requests.Response) -> Iterator[ChatResult]:
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = ((payload.get("choices") or [{}])[0]).get("delta") or {}
        content = delta.get("content")
        if content:
            yield ChatResult(message=ProviderMessage(content=content), raw=payload)
