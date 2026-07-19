"""GPU/RAM probing and Ollama model tier recommendations."""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any, Dict, List, Optional


def _probe_nvidia_gpu() -> Dict[str, Any]:
    """Return vramMb, vramUsedMb, gpuUtilPct when nvidia-smi is available."""
    empty: Dict[str, Any] = {"vramMb": None, "vramUsedMb": None, "gpuUtilPct": None}
    if not shutil.which("nvidia-smi"):
        return empty
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return empty
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            return empty
        parts = [p.strip() for p in lines[0].split(",")]
        if len(parts) < 3:
            return empty
        return {
            "vramMb": int(float(parts[0])),
            "vramUsedMb": int(float(parts[1])),
            "gpuUtilPct": int(float(parts[2])),
        }
    except Exception:
        return empty


def _probe_nvidia_vram_mb() -> Optional[int]:
    return _probe_nvidia_gpu().get("vramMb")


def _probe_system_ram_gb() -> Optional[float]:
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024**3), 1)
        except Exception:
            pass
    return None


def probe_system_capacity() -> Dict[str, Any]:
    gpu = _probe_nvidia_gpu()
    vram_mb = gpu.get("vramMb")
    ram_gb = _probe_system_ram_gb()
    tier = "cpu_only"
    if vram_mb is not None:
        if vram_mb >= 24000:
            tier = "high"
        elif vram_mb >= 12000:
            tier = "medium"
        elif vram_mb >= 8000:
            tier = "low"
        else:
            tier = "minimal"
    return {
        "gpuAvailable": vram_mb is not None,
        "vramMb": vram_mb,
        "vramUsedMb": gpu.get("vramUsedMb"),
        "gpuUtilPct": gpu.get("gpuUtilPct"),
        "ramGb": ram_gb,
        "platform": platform.system(),
        "tier": tier,
    }


def get_model_recommendations(
    capacity: Dict[str, Any],
    *,
    installed_models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    installed = set(installed_models or [])
    tier = str(capacity.get("tier") or "cpu_only")
    vram_mb = capacity.get("vramMb")

    if tier in ("high",) or (isinstance(vram_mb, int) and vram_mb >= 24000):
        dev = "qwen2.5-coder:14b"
        small = "qwen2.5-coder:7b"
    elif tier in ("medium",) or (isinstance(vram_mb, int) and vram_mb >= 12000):
        dev = "qwen2.5-coder:14b"
        small = "qwen2.5-coder:7b"
    elif tier in ("low",) or (isinstance(vram_mb, int) and vram_mb >= 8000):
        dev = "qwen2.5-coder:7b"
        small = "qwen2.5-coder:7b"
    else:
        dev = "qwen2.5-coder:7b"
        small = "llama3:8b"

    roles = {
        "po": small,
        "dev": dev,
        "cr": small,
        "qa": small,
    }

    def _status(model: str) -> str:
        if model in installed:
            return "installed"
        for name in installed:
            if name.startswith(model.split(":")[0]):
                return "partial"
        return "not_installed"

    return {
        "capacity": capacity,
        "tier": tier,
        "roles": {
            role: {"model": model, "status": _status(model)} for role, model in roles.items()
        },
        "note": (
            "Quantized tags (e.g. :q4_K_M) reduce VRAM use. "
            "Recommendations assume a single loaded model."
        ),
    }
