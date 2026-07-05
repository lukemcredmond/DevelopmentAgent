"""Web search tool — DuckDuckGo HTML lite (no API key required)."""

import os
import re
from html import unescape
from typing import List
from urllib.parse import quote_plus

import requests

from backend.services.workflow_settings import get_workflow_settings

MAX_SNIPPET_CHARS = 500
DEFAULT_MAX_RESULTS = 5


def _parse_ddg_html(html: str, max_results: int) -> List[str]:
    snippets: List[str] = []
    for match in re.finditer(
        r'class="result__a"[^>]*>([^<]+)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        title = unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        body = unescape(re.sub(r"<[^>]+>", " ", match.group(2))).strip()
        body = re.sub(r"\s+", " ", body)[:MAX_SNIPPET_CHARS]
        if title or body:
            snippets.append(f"{title}\n{body}".strip())
        if len(snippets) >= max_results:
            break
    return snippets


def _search_serper(query: str, api_key: str, max_results: int) -> List[str]:
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": max_results},
        timeout=12,
    )
    resp.raise_for_status()
    data = resp.json()
    snippets: List[str] = []
    for item in (data.get("organic") or [])[:max_results]:
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("snippet", "")).strip()[:MAX_SNIPPET_CHARS]
        if title or snippet:
            snippets.append(f"{title}\n{snippet}".strip())
    return snippets


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    ws = get_workflow_settings()
    if not ws.get("enableWebSearch"):
        return "Error: web search is disabled — enable it in Workflow settings (enableWebSearch)."

    q = (query or "").strip()
    if not q:
        return "Error: query is required."

    limit = min(int(max_results or DEFAULT_MAX_RESULTS), DEFAULT_MAX_RESULTS)
    api_key = os.environ.get("WEB_SEARCH_API_KEY", "").strip()

    try:
        if api_key:
            snippets = _search_serper(q, api_key, limit)
        else:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": q},
                headers={"User-Agent": "DevelopmentAgent/1.0 (+local dev agent)"},
                timeout=12,
            )
            resp.raise_for_status()
            snippets = _parse_ddg_html(resp.text, limit)

        if not snippets:
            return f"No web results for '{q}'."

        lines = [f"Web search: '{q}' ({len(snippets)} result(s))"]
        for i, snippet in enumerate(snippets, 1):
            lines.append(f"\n--- Result {i} ---\n{snippet}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: web search failed: {exc}"
