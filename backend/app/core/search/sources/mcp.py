"""arxiv-mcp-server as a PaperGraph search source.

Consumes the external `arxiv-mcp-server` (https://github.com/blazickjp/arxiv-mcp-server)
over stdio, calling its `search_papers` MCP tool. Results are converted to
PaperGraph's `Paper` model with `source="mcp"`.

Design notes
------------
- **Spawn-per-call**: each `_search_mcp_src` call spawns its own stdio server
  subprocess and tears it down after. `search_async` is invoked both from the
  app event loop (async routes) and via `run_coroutine_sync` (a fresh loop per
  call) — a persistent cross-call MCP session would break across loops. Per-call
  spawn is correct at the cost of ~0.5s startup. The source is opt-in
  (`PAPERGRAPH_MCP_ARXIV_ENABLED`), so normal searches are unaffected.
- **Opt-in**: disabled by default. Enable via settings or env
  `PAPERGRAPH_MCP_ARXIV_ENABLED=true`.
- arxiv-mcp-server's `search_papers` queries the arXiv API, which overlaps with
  the native `arxiv` source. This adapter's value is architectural: a pluggable
  MCP search source so future MCP servers (Semantic Scholar, PubMed) can slot in
  with the same pattern.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from ....models.schemas import PaperSource
from ....settings import get_settings
from ...author import Author
from ...paper import Paper

logger = logging.getLogger(__name__)

# Strip the "[EXTERNAL CONTENT] " prefix arxiv-mcp-server prepends to abstracts.
_EXT_PREFIX_RE = re.compile(r"^\s*\[EXTERNAL CONTENT\]\s*", re.IGNORECASE)


def _mcp_enabled() -> bool:
    try:
        return bool(get_settings().mcp_arxiv_enabled)
    except Exception:
        return False


def _resolve_server_command() -> str:
    cfg = get_settings().mcp_arxiv_command.strip()
    if cfg:
        return cfg
    # Default: look in the active Python env's bin directory.
    env_bin = os.path.join(os.path.dirname(sys.executable), "arxiv-mcp-server")
    return env_bin if os.path.exists(env_bin) else "arxiv-mcp-server"


def _resolve_storage_path() -> str:
    cfg = get_settings().mcp_arxiv_storage_path.strip()
    if cfg:
        return cfg
    return os.path.join(get_settings().data_dir, "mcp_arxiv_storage")


def _parse_year(published: str | None) -> int | None:
    if not published:
        return None
    try:
        return int(published[:4])
    except (TypeError, ValueError):
        return None


def _clean_arxiv_id(raw: str | None) -> str | None:
    if not raw:
        return None
    aid = str(raw).strip()
    # MCP returns ids like "2412.16738v1"; strip the version suffix.
    aid = re.sub(r"v\d+$", "", aid, flags=re.I)
    return aid or None


def _mcp_paper_to_paper(searcher: Any, p: Dict[str, Any]) -> Optional[Paper]:
    """Convert one MCP search result dict to a Paper (source=mcp)."""
    title = str(p.get("title") or "").strip()
    if not title:
        return None
    abstract = _EXT_PREFIX_RE.sub("", str(p.get("abstract") or "")).strip()
    authors = [Author(name=str(a)) for a in (p.get("authors") or []) if str(a).strip()]
    arxiv_id = _clean_arxiv_id(p.get("id"))
    categories = p.get("categories") or []
    primary_cat = str(categories[0]) if categories else ""
    journal = f"arXiv:{primary_cat}" if primary_cat else "arXiv"
    year = _parse_year(p.get("published"))
    pdf_url = str(p.get("url") or "").strip() or None
    source_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else pdf_url
    try:
        return searcher._make_paper(
            title=title,
            authors=authors,
            abstract=abstract or None,
            arxiv_id=arxiv_id,
            journal=journal,
            year=year,
            pdf_url=pdf_url,
            source_url=source_url,
            source=PaperSource.MCP.value,
        )
    except Exception:
        logger.debug("mcp_paper_to_paper_failed", exc_info=True)
        return None


async def _mcp_search_papers(query: str, max_results: int, **kwargs: Any) -> List[Dict[str, Any]]:
    """Spawn arxiv-mcp-server stdio, call search_papers, return raw paper dicts."""
    from contextlib import AsyncExitStack
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = _resolve_server_command()
    storage = _resolve_storage_path()
    os.makedirs(storage, exist_ok=True)
    params = StdioServerParameters(command=command, args=["--storage-path", storage])

    args: Dict[str, Any] = {"query": (query or "").strip(), "max_results": int(max_results)}
    date_from = kwargs.get("date_from") or kwargs.get("arxiv_date_from")
    if date_from:
        args["date_from"] = str(date_from)
    cats = kwargs.get("arxiv_categories") or kwargs.get("categories")
    if isinstance(cats, list) and cats:
        args["categories"] = [str(c) for c in cats if str(c).strip()]
    sort_by = kwargs.get("mcp_sort_by")
    if sort_by:
        args["sort_by"] = str(sort_by)

    stack = AsyncExitStack()
    try:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.call_tool("search_papers", args)
        if getattr(result, "isError", False):
            logger.warning("mcp_arxiv search_papers returned is_error")
            return []
        papers: List[Dict[str, Any]] = []
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", "") != "text":
                continue
            text = getattr(block, "text", "") or ""
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                ps = data.get("papers")
                if isinstance(ps, list):
                    papers.extend(p for p in ps if isinstance(p, dict))
            elif isinstance(data, list):
                papers.extend(p for p in data if isinstance(p, dict))
        return papers
    finally:
        await stack.aclose()


async def _search_mcp_src(searcher: Any, query: str, max_results: int, **kwargs: Any) -> List[Paper]:
    """MCP arxiv source — opt-in. Returns Papers with source=mcp."""
    if not _mcp_enabled() or not (query or "").strip():
        return []
    try:
        raw_papers = await _mcp_search_papers(query, max_results, **kwargs)
    except Exception as e:
        logger.warning("mcp_arxiv search failed: %s", e)
        return []
    out: List[Paper] = []
    for p in raw_papers:
        paper = _mcp_paper_to_paper(searcher, p)
        if paper is not None:
            out.append(paper)
    searcher._bump_stat("mcp_requests")
    logger.info("从 mcp 获取 %s 篇文献", len(out))
    return out
