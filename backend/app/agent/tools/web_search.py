"""Real web search for the agent.

Default engine is the open-source ``ddgs`` metasearch (DuckDuckGo, Brave,
Wikipedia, and other public backends — the same class of aggregator used as
a Tavily/Exa stand-in). Optional upgrades, in order:

- ``SEARXNG_URL`` — self-hosted SearXNG (what most OSS agent stacks use)
- ``TAVILY_API_KEY`` — Tavily (ChatGPT-style research API)
- ``BRAVE_SEARCH_API_KEY`` — Brave Search
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpx

from app.agent.events import Artifact, ToolResult
from app.agent.tools.base import Tool, ToolContext, output_bound_bytes

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
MAX_REDIRECTS = 5


def _clean(text: str, limit: int = 400) -> str:
    return _WS.sub(" ", (text or "").strip())[:limit]


def _safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False
    if host.startswith("10.") or host.startswith("192.168.") or host.startswith("169.254."):
        return False
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "172":
        try:
            if 16 <= int(parts[1]) <= 31:
                return False
        except ValueError:
            pass
    return True


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.sixtofour is not None or ip.teredo is not None:
            return False
        ip = ip.ipv4_mapped or ip
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


async def _public_address(url: str) -> str | None:
    """The address to connect to, or None unless every resolved address is public."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        resolved = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    addresses = [item[4][0] for item in resolved]
    return addresses[0] if addresses and all(_is_public_ip(a) for a in addresses) else None


def _pinned_request(client: httpx.AsyncClient, url: str, address: str) -> httpx.Request:
    """A request that connects to the checked address, so DNS cannot change after the check."""
    parts = urlsplit(url)
    literal = f"[{address}]" if ":" in address else address
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    return client.build_request("GET", urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, "")),
                                headers={"Host": host}, extensions={"sni_hostname": parts.hostname})


def _format_hits(hits: list[dict]) -> str:
    if not hits:
        return "No web results."
    lines = []
    for i, hit in enumerate(hits, 1):
        title = hit.get("title") or hit.get("href") or "untitled"
        url = hit.get("href") or hit.get("url") or ""
        body = hit.get("body") or hit.get("snippet") or hit.get("content") or ""
        lines.append(f"{i}. {title}\n   {url}\n   {_clean(str(body), 280)}")
    return "\n".join(lines)


async def _searxng(base: str, query: str, limit: int) -> list[dict]:
    url = base.rstrip("/") + "/search"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, params={"q": query, "format": "json", "categories": "general"})
        r.raise_for_status()
        rows = (r.json() or {}).get("results") or []
    out = []
    for row in rows[:limit]:
        href = str(row.get("url") or "")
        if not _safe_url(href):
            continue
        out.append({"title": row.get("title") or href, "href": href, "body": row.get("content") or ""})
    return out


async def _tavily(key: str, query: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": limit, "search_depth": "basic"},
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("results") or []
    return [
        {"title": row.get("title") or row.get("url"), "href": row.get("url"), "body": row.get("content") or ""}
        for row in rows[:limit]
        if _safe_url(str(row.get("url") or ""))
    ]


async def _brave(key: str, query: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={"Accept": "application/json", "X-Subscription-Token": key},
        )
        r.raise_for_status()
        rows = ((r.json() or {}).get("web") or {}).get("results") or []
    return [
        {"title": row.get("title") or row.get("url"), "href": row.get("url"), "body": row.get("description") or ""}
        for row in rows[:limit]
        if _safe_url(str(row.get("url") or ""))
    ]


def _ddgs(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS

    rows = list(DDGS().text(query, max_results=limit))
    out = []
    for row in rows:
        href = str(row.get("href") or row.get("url") or "")
        if not _safe_url(href):
            continue
        out.append({"title": row.get("title") or href, "href": href, "body": row.get("body") or ""})
    return out


async def search_web(query: str, limit: int, settings) -> tuple[list[dict], str]:
    limit = max(1, min(int(limit or 8), 12))
    searx = getattr(settings, "searxng_url", None)
    if searx:
        try:
            return await _searxng(str(searx), query, limit), "searxng"
        except Exception:  # noqa: BLE001
            logger.exception("SearXNG search failed")
    tavily = getattr(settings, "tavily_api_key", None)
    if tavily:
        try:
            return await _tavily(tavily, query, limit), "tavily"
        except Exception:  # noqa: BLE001
            logger.exception("Tavily search failed")
    brave = getattr(settings, "brave_search_api_key", None)
    if brave:
        try:
            return await _brave(brave, query, limit), "brave"
        except Exception:  # noqa: BLE001
            logger.exception("Brave search failed")
    # DDGS is synchronous. Keep its network work off the event loop so running
    # activity, sibling completions, pause and Stop remain responsive.
    return await asyncio.to_thread(_ddgs, query, limit), "ddgs"


class WebSearchTool(Tool):
    name = "web_search"
    read_only = True
    destructive = False
    concurrent_safe = True
    side_effect_scope = "none"   # nothing durable changes; the query leaves the machine
    risk_level = "low"
    needs_approval = False
    # The harness enables this capability by default. Explicit API opt-outs
    # keep the gate closed unless a slash command or steer requests the tool.
    requires_flags = ("web_search",)
    # No enforced bound: the engine chain (SearXNG → Tavily → Brave → ddgs) is
    # tried in order, each with its own 20s client timeout.
    background = True
    ui_kind = "search"
    description = (
        "Search the live web automatically when an answer needs real-time or up-to-date information: "
        "news, weather, prices, schedules, current officeholders, recent releases, or changing facts. "
        "Also use for explicit requests to search, check online, or verify current sources. "
        "No slash command or manual selection is required. Skip browsing for stable explanations or when the user asks not to browse. "
        "Returns ranked titles, URLs, and snippets. Follow interesting URLs with web_fetch."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "1–12 results (default 8)"},
        },
        "required": ["query"],
    }

    async def run(self, ctx: ToolContext, query: str = "", max_results: int = 8, **_: Any) -> ToolResult:
        q = (query or "").strip()
        if not q:
            return ToolResult(ok=False, output="", error="query is required")
        await ctx.progress(f"Searching: {q}")
        hits, engine = await search_web(q, max_results, ctx.settings)
        text = _format_hits(hits)
        return ToolResult(
            ok=True,
            output=text,
            artifacts=[
                Artifact(kind="json", name="search.json", data={"query": q, "engine": engine, "results": hits})
            ],
            meta={"engine": engine, "count": len(hits), "query": q,
                  "public_sources": [{"title": hit.get("title", "Source"), "url": hit.get("url") or hit.get("href")} for hit in hits]},
        )


class WebFetchTool(Tool):
    name = "web_fetch"
    read_only = True
    destructive = False
    concurrent_safe = True
    side_effect_scope = "none"   # reads a public page; local/private URLs are rejected
    risk_level = "low"
    needs_approval = False
    requires_flags = ("web_search",)   # same turn switch as web_search
    timeout_ms = 20_000          # httpx.AsyncClient(timeout=20.0)
    max_output_bytes = output_bound_bytes(8_000)   # text capped at 8_000 chars
    background = True
    ui_kind = "search"
    description = (
        "Fetch a public http(s) page and return cleaned text. Use after web_search to read a source. "
        "Local and private network URLs are rejected."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "https URL to fetch"},
        },
        "required": ["url"],
    }

    async def run(self, ctx: ToolContext, url: str = "", **_: Any) -> ToolResult:
        target = (url or "").strip()
        await ctx.progress(f"Fetching {target}")
        current = target
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers={"User-Agent": "MatOpt/1.0"}) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                address = await _public_address(current) if _safe_url(current) else None
                if address is None:
                    return ToolResult(ok=False, output="", error="Only public http(s) URLs can be fetched.")
                r = await client.send(_pinned_request(client, current, address))
                if not r.has_redirect_location:
                    break
                current = urljoin(current, r.headers["location"])
            else:
                return ToolResult(ok=False, output="", error="Too many redirects.")
            r.raise_for_status()
            raw = r.text or ""
        text = _WS.sub(" ", _TAG.sub(" ", raw)).strip()[:8_000]
        return ToolResult(ok=True, output=f"{target}\n\n{text}", meta={"url": target, "bytes": len(raw)})
