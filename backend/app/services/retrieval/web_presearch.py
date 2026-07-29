"""Web 预搜索：在多源学术检索之前先做"锚点"召回。

目标：
- 解决短词/术语（如 patchcore）导致的多源召回噪声与歧义
- 先从 Web 搜索拿到最可信的论文标题/DOI/arXiv，再由 Agent 生成更精确的检索单元

说明：
- Settings 默认 ``tavily_presearch_enabled=true``；未配置 ``TAVILY_API_KEY`` 时不会发外呼。
- Tavily 会场→域名映射见 ``tavily_venue_domains.json``（``tavily_venue_config``），勿在此文件堆业务映射。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Tavily：query 超过 400 字符会返回 400（见官方文档与常见报错）
TAVILY_MAX_QUERY_CHARS = 400


def _normalize_tavily_query(query: str, *, max_chars: int = TAVILY_MAX_QUERY_CHARS) -> str:
    q = (query or "").strip()
    if not q:
        return ""
    if len(q) <= max_chars:
        return q
    clipped = q[:max_chars].rstrip()
    logger.warning(
        "tavily: query 过长已截断 (%d -> %d 字符)，避免 Tavily 400",
        len(q),
        len(clipped),
    )
    return clipped


async def tavily_search_async(
    *,
    api_key: str,
    query: str,
    max_results: int = 5,
    timeout_sec: int = 20,
    include_domains: Optional[List[str]] = None,
    httpx_client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """Async Tavily Search API call. Reuses shared httpx client when available."""
    q = _normalize_tavily_query(query)
    if not q:
        return []
    if not (api_key or "").strip():
        return []

    n = max(1, min(10, int(max_results or 5)))
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": q,
        "max_results": n,
        "include_answer": True,
        "include_raw_content": True,
    }
    dom = [str(x).strip() for x in (include_domains or []) if str(x).strip()][:3]
    if dom:
        payload["include_domains"] = dom

    timeout = httpx.Timeout(timeout_sec)
    async def _do_post(client):
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            payload2 = dict(payload)
            payload2["include_raw_content"] = False
            resp = await client.post(url, json=payload2)
        resp.raise_for_status()
        return resp.json() or {}

    if httpx_client is not None:
        data = await _do_post(httpx_client)
    else:
        async with httpx.AsyncClient(timeout=timeout) as client:
            data = await _do_post(client)

    out: List[Dict[str, Any]] = []
    ans = str(data.get("answer") or "").strip()
    if ans:
        out.append({"title": ans[:180], "link": "", "snippet": ans})
    for it in (data.get("results") or [])[:n]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        link = str(it.get("url") or "").strip()
        snippet = str(it.get("content") or "").strip()
        if not title and not link and not snippet:
            continue
        out.append({
            "title": title[:200] if title else "",
            "link": link,
            "snippet": snippet[:300] if snippet else "",
            "raw_content": str(it.get("raw_content") or "")[:20000],
        })
    return out


def pick_anchor_title(items: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the best paper title from Tavily results. Prefer trusted academic sources."""
    if not items:
        return None

    trusted = ("arxiv.org", "doi.org", "neurips.cc", "openreview.net", "proceedings.")
    def _score(it: Dict[str, Any]) -> float:
        title = str(it.get("title") or "").strip()
        if not title or len(title) < 8:
            return -1e9
        link = str(it.get("link") or it.get("url") or "").lower()
        score = float(len(title))
        if any(h in link for h in trusted):
            score += 200.0
        if any(h in title.lower() for h in ("github", "repo", "awesome-")):
            score -= 500.0
        return score

    best = max(items, key=_score)
    title = str(best.get("title") or "").strip()
    title = re.sub(r"^\s*(\[PDF\]|\(PDF\))\s*", "", title, flags=re.I)
    return title or None

_ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)\s*([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.I)


def extract_anchor_ids(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """从 Tavily 返回里提取高置信 ID（arXiv / DOI）。

    用途：当 query 是短词/术语时，用这些 ID 作为"最匹配"的强证据加入候选集，
    但不绑定到某个具体 query（避免硬编码）。
    """
    arxiv_ids: List[str] = []
    dois: List[str] = []

    def _push_unique(buf: List[str], x: str, limit: int):
        t = (x or "").strip()
        if not t:
            return
        tl = t.lower()
        if any(y.lower() == tl for y in buf):
            return
        buf.append(t)
        if len(buf) > limit:
            del buf[limit:]

    for it in (items or [])[:10]:
        if not isinstance(it, dict):
            continue
        hay = " ".join(
            [
                str(it.get("title") or ""),
                str(it.get("link") or it.get("url") or ""),
                str(it.get("snippet") or it.get("content") or ""),
                str(it.get("raw_content") or ""),
            ]
        )
        for m in _ARXIV_ID_RE.finditer(hay):
            _push_unique(arxiv_ids, m.group(1), 5)
        for m in _DOI_RE.finditer(hay):
            doi = m.group(0).rstrip(").,;]")
            _push_unique(dois, doi, 5)

    return {"arxiv_ids": arxiv_ids, "dois": dois}


