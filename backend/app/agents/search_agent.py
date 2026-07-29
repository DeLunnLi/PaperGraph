
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from cachetools import TTLCache

from app.exceptions import IntentParseError, LLMError

from ..core.search.paper_searcher import _sanitize_author_list_for_query
from ..models.schemas import Paper
from ..services.search_intent import (
    apply_llm_intent_hygiene,
    extract_json_object,
    finalize_llm_intent,
    search_intent_from_dict,
)
from ..services.search_intent.arxiv_normalization import extract_arxiv_ids_from_text
from ..services.search_intent.parsing import _ensure_intent_year_window_ordered
from ..settings import get_settings
from .base import BaseAgent
from .prompts.search import INTENT_LLM_PROMPT as _INTENT_LLM_PROMPT_TEMPLATE
from .support import SearchExplainer as _SearchExplainer
from .support.search_models import SearchIntent

logger = logging.getLogger(__name__)

# TTL cache for parsed search intents: 5 min TTL, max 200 entries.
# cachetools TTLCache is not thread-safe; guard get/set with a lock so a
# concurrent expire-while-read can't raise KeyError out of understand_intent.
_INTENT_CACHE: TTLCache[tuple[str, str], SearchIntent] = TTLCache(maxsize=200, ttl=300)
_INTENT_CACHE_LOCK = threading.Lock()


class SearchAgent(BaseAgent):
    INTENT_LLM_PROMPT = _INTENT_LLM_PROMPT_TEMPLATE

    def __init__(self) -> None:
        super().__init__()
        self.intent_parser = IntentParser(self)
        self.explainer = _SearchExplainer()

    def _parse_intent_from_llm_step(
        self,
        *,
        message: str,
        profile: str,
        prompt_template: Optional[str] = None,
        correction_hint: Optional[str] = None,
    ) -> SearchIntent:
        msg = (message or "").strip()
        if not msg:
            raise ValueError("intent_parse_empty_message")
        if not self.llm:
            raise LLMError("intent_parse_llm_unavailable", code="intent_parse_llm_unavailable")

        from ..services.search_intent.parsing import format_intent_llm_prompt

        tmpl = prompt_template or self.INTENT_LLM_PROMPT
        prompt = format_intent_llm_prompt(
            tmpl, msg, profile, correction_hint=(correction_hint or "").strip() or None
        )
        from ..services.llm.agent_runtime import stateless_llm_chat

        resp = stateless_llm_chat(
            self.llm,
            "你是学术检索意图解析器。只输出 JSON，不要解释。",
            prompt,
        )
        text = (resp or "").strip()
        payload = extract_json_object(text)
        if not payload:
            err: ValueError = ValueError("LLM 未返回有效 JSON")
            setattr(err, "last_llm_output", text)
            raise err

        intent = search_intent_from_dict(payload)
        return finalize_llm_intent(intent, profile)

    def understand_intent(self, message: str, profile: str = "accuracy") -> SearchIntent:
        # Explicit arXiv IDs are already a complete, unambiguous search plan. Avoid
        # spending an LLM round-trip on them and keep the exact ID authoritative.
        explicit_ids = extract_arxiv_ids_from_text(message)
        if explicit_ids:
            return SearchIntent(
                query=explicit_ids[0],
                raw_user_message=(message or "").strip()[:3200],
                arxiv_id_list=explicit_ids[:8],
                sources=["arxiv"],
                search_strategy="targeted_lookup",
                use_llm_rank=False,
                rerank_recall_max=max(8, len(explicit_ids)),
                max_results=max(5, min(30, len(explicit_ids))),
            )

        # Inject shared cross-agent memory as context hints
        # This lets search benefit from user's reading history and preferences
        # Filter by tags: search agent cares about reader insights and previous searches
        shared_hints = self._read_shared_recent(
            memory_types=["working", "episodic"], limit=5,
            tags=["reader", "search", "deep_search"],
        )
        if shared_hints:
            # Append shared hints to the message as soft context (non-intrusive)
            hint_text = " | ".join(shared_hints[:3])[:200]
            logger.debug("[SearchAgent] shared memory hints: %s", hint_text[:80])
        return self.intent_parser.parse(message, profile=profile)

    def explain_results(self, intent: SearchIntent, papers: list[Paper], mode: str = "accuracy") -> str:
        # Write search insights to shared memory for other agents (e.g., reader, daily)
        try:
            if papers and intent.query:
                top_titles = "; ".join(str(getattr(p, "title", "") or "")[:40] for p in papers[:3])
                self._write_shared(
                    content=f"[搜索] {intent.query[:60]} → 找到{len(papers)}篇: {top_titles}",
                    memory_type="working",
                    importance=0.5,
                    tags=["search"],
                )
        except Exception:
            pass
        _ = mode
        return self.explainer.format_search_explanation(intent, papers)

    @staticmethod
    def _normalize_profile(profile: Optional[str]) -> str:
        prof = (profile or "accuracy").strip().lower()
        return prof if prof in ("accuracy", "novelty") else "accuracy"


class IntentParser:
    def __init__(self, agent: SearchAgent) -> None:
        self._agent = agent

    def parse(self, message: str, profile: str = "accuracy") -> SearchIntent:
        msg = (message or "").strip()
        if not msg:
            return SearchIntent()

        cache_key = (msg.lower()[:200], (profile or "accuracy").strip().lower())
        with _INTENT_CACHE_LOCK:
            cached = _INTENT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        intent = self._parse_with_retry(msg, profile)
        with _INTENT_CACHE_LOCK:
            _INTENT_CACHE[cache_key] = intent
        return intent

    def _parse_with_retry(self, msg: str, profile: str) -> SearchIntent:
        from ..services.search_intent.parsing import build_intent_retry_correction_hint

        prof = self._agent._normalize_profile(profile)
        if not self._agent.llm:
            raise LLMError("search_agent_llm_unavailable", code="search_agent_llm_unavailable")
        s = get_settings()
        outer_retries = max(0, min(5, int(getattr(s, "papergraph_intent_parse_max_retries", 2) or 2)))
        correction: str | None = None
        last_exc: Exception | None = None
        last_output: str | None = None
        for attempt in range(outer_retries + 1):
            try:
                return self._parse_llm_primary(msg, prof, correction_hint=correction)
            except (RuntimeError, ValueError, TypeError) as e:
                last_exc = e
                last_output = getattr(e, "last_llm_output", None) or last_output
                logger.warning(
                    "[SearchAgent] intent parse failed (attempt %d/%d): %s",
                    attempt + 1,
                    outer_retries + 1,
                    e,
                )
                if attempt >= outer_retries:
                    break
                if "connection error" in str(e or "").lower() or "timed out" in str(e or "").lower():
                    break
                correction = build_intent_retry_correction_hint(
                    e, user_message=msg, last_output=last_output
                )
        logger.warning("[SearchAgent] LLM intent parse exhausted retries: %s", last_exc)
        raise IntentParseError("search_agent_intent_failed", code="search_agent_intent_failed") from last_exc

    def _parse_llm_primary(
        self,
        msg: str,
        prof: str,
        *,
        correction_hint: str | None = None,
    ) -> SearchIntent:
        llm_intent = self._agent._parse_intent_from_llm_step(
            message=msg,
            profile=prof,
            prompt_template=self._agent.INTENT_LLM_PROMPT,
            correction_hint=correction_hint,
        )
        out = finalize_llm_intent(llm_intent, prof)
        if not (out.query or "").strip() and (out.keywords or []):
            out.query = (out.keywords[0] or "")[:500]
        if not (out.query or "").strip() and (out.authors or []):
            out.query = str(out.authors[0]).strip()[:500]
        if (
            not (out.query or "").strip()
            and not (out.venues or [])
            and not (out.authors or [])
            and not (out.arxiv_id_list or [])
            and not (out.target_titles or [])
        ):
            raise ValueError("intent_parse_empty_query")
        apply_llm_intent_hygiene(out, msg)
        _ensure_intent_year_window_ordered(out)
        out.raw_user_message = msg.strip()[:3200]
        out.authors = _sanitize_author_list_for_query(out.query or "", out.authors or [])
        return out


_search_agent_singleton: Optional[SearchAgent] = None


def get_search_agent() -> SearchAgent:
    global _search_agent_singleton
    if _search_agent_singleton is None:
        _search_agent_singleton = SearchAgent()
    return _search_agent_singleton
