import requests
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/ollama/health")
def ollama_health(url: str = "http://localhost:11434"):
    try:
        response = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        if response.status_code == 200:
            data = response.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {"ok": True, "url": url, "models": models}
        return {"ok": False, "url": url, "error": f"HTTP {response.status_code}"}
    except requests.RequestException as e:
        return {"ok": False, "url": url, "error": str(e)}
