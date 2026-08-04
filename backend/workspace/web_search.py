"""Web search tool — DuckDuckGo HTML lite (no API key required)."""

from __future__ import annotations

import os
import re
from html import unescape
from typing import List, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import requests

from backend.services.workflow_settings import get_workflow_settings

MAX_SNIPPET_CHARS = 500
DEFAULT_MAX_RESULTS = 5
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


def _unwrap_ddg_redirect(href: str) -> str:
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href and "uddg=" in href:
        parsed = urlparse(href)
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _ddg_blocked(html: str) -> bool:
    lower = (html or "").lower()
    markers = (
        "challenge-form",
        "anomaly-modal",
        "bots use duckduckgo",
        "please complete the following challenge",
        "if this error persists",
    )
    return any(m in lower for m in markers)


def _ddg_has_result_markers(html: str) -> bool:
    return bool(
        re.search(r'result__a|result__title|class="result"', html or "", re.IGNORECASE)
    )


def _parse_ddg_html(html: str, max_results: int) -> List[str]:
    """Parse DuckDuckGo HTML lite — tolerant of layout changes."""
    snippets: List[str] = []
    seen: set[str] = set()

    link_patterns = (
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
    )
    pairs: List[Tuple[str, str]] = []
    for pattern in link_patterns:
        pairs = [
            (href, title_html)
            for href, title_html in re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        ]
        if pairs:
            break

    snippet_htmls = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div|span)>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for idx, (href, title_html) in enumerate(pairs):
        if len(snippets) >= max_results:
            break
        title = _strip_html(title_html)
        if not title:
            continue
        url = _unwrap_ddg_redirect(href)
        body = ""
        if idx < len(snippet_htmls):
            body = _strip_html(snippet_htmls[idx])[:MAX_SNIPPET_CHARS]
        key = f"{title}|{url}"
        if key in seen:
            continue
        seen.add(key)
        block = title
        if url:
            block += f"\n{url}"
        if body:
            block += f"\n{body}"
        snippets.append(block.strip())

    return snippets


def _fetch_ddg_html(query: str) -> str:
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    last_text = ""
    for method in ("get", "post"):
        try:
            if method == "get":
                resp = requests.get(
                    _DDG_HTML_URL,
                    params={"q": query},
                    headers=headers,
                    timeout=12,
                )
            else:
                resp = requests.post(
                    _DDG_HTML_URL,
                    data={"q": query, "b": "", "kl": "wt-wt"},
                    headers=headers,
                    timeout=12,
                )
            resp.raise_for_status()
            last_text = resp.text or ""
            if _parse_ddg_html(last_text, 1):
                return last_text
        except Exception:
            continue
    return last_text


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
        link = str(item.get("link", "")).strip()
        snippet = str(item.get("snippet", "")).strip()[:MAX_SNIPPET_CHARS]
        block = title
        if link:
            block += f"\n{link}"
        if snippet:
            block += f"\n{snippet}"
        if title or snippet:
            snippets.append(block.strip())
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
        html = ""
        if api_key:
            snippets = _search_serper(q, api_key, limit)
            provider = "Serper"
        else:
            html = _fetch_ddg_html(q)
            if _ddg_blocked(html):
                return (
                    "Error: DuckDuckGo blocked the search request (bot check). "
                    "Set environment variable WEB_SEARCH_API_KEY for Serper (google.serper.dev), "
                    "or retry later from a different network."
                )
            snippets = _parse_ddg_html(html, limit)
            provider = "DuckDuckGo"

        if not snippets:
            if not api_key and html and _ddg_has_result_markers(html):
                return (
                    f"Error: web search could not parse results for '{q}' ({provider}). "
                    "Try WEB_SEARCH_API_KEY for Serper, or rephrase the query."
                )
            return f"Error: No web results for '{q}'."

        lines = [f"Web search ({provider}): '{q}' ({len(snippets)} result(s))"]
        for i, snippet in enumerate(snippets, 1):
            lines.append(f"\n--- Result {i} ---\n{snippet}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: web search failed: {exc}"
