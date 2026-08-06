"""ReaderToolsMixin — extracted from PaperAnalysisAgent (see app/agents/paper_analysis_agent.py)."""
from __future__ import annotations

import logging

from typing import Any, Callable, Dict, List, Optional, Tuple
from ..prompts.paper_analysis import ANALYSIS_SYSTEM, READER_CHAT_SYSTEM
from .reader_paper_lookup_tool import ReaderPaperLookupTool, ground_score_paper_vs_reference_blob
from .reader_pdf_parse_tool import ReaderPdfParseTool
from .reader_reference_lookup_tool import (
    READER_RECOMMEND_MAX_RESULTS, ReaderReferenceLookupTool,
    READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_REF_BLOCK,
    paper_matches_reader_snap, parse_reader_recommendation_intent,
    prioritize_reader_related_pairs_refs_first, reader_user_allows_external_paper_lookup,
    rerank_reader_pairs_by_anchor_refs_first, resolve_references_via_openalex,
    user_message_may_need_reference_lookup,
    READER_RELATED_FROM_PRE_SEARCH,
)
from .reader_table_tool import ReaderTableTool
from hello_agents import SimpleAgent, ToolRegistry
from ...services.llm.agent_config import papergraph_agent_config


logger = logging.getLogger(__name__)


class ReaderToolsMixin:
    def _new_reader_agent(self, ctx: "ReaderCtx") -> SimpleAgent:
        """每请求新建 reader SimpleAgent，工具回调绑定到 ctx（并发隔离）。"""
        reg = ToolRegistry()
        reg.register_tool(ReaderPaperLookupTool(
            on_papers_found=lambda papers, src: self._reader_tool_on_found(ctx, papers, src),
            get_snap=lambda: ctx.snap,
        ))
        reg.register_tool(ReaderReferenceLookupTool(
            get_snap=lambda: ctx.snap,
            on_papers_found=lambda papers, src: self._reader_tool_on_found(ctx, papers, src),
            get_user_message=lambda: ctx.user_message,
        ))
        reg.register_tool(ReaderPdfParseTool(
            get_snap=lambda: ctx.snap,
            on_parsed=lambda obj: self._reader_on_pdf_structure(ctx, obj),
        ))
        reg.register_tool(ReaderTableTool(get_snap=lambda: ctx.snap))
        return SimpleAgent(
            name="papergraph_paper_reader",
            llm=self.llm,
            system_prompt=READER_CHAT_SYSTEM,
            config=papergraph_agent_config(),
            tool_registry=reg,
            enable_tool_calling=True,
            max_tool_iterations=5,
        )
    def _reader_tool_on_found(self, ctx: "ReaderCtx", papers: List[Any], source: str) -> None:
        with ctx.lookup_lock:
            for p in papers or []:
                ctx.lookup_buffer.append((p, source))
    def _reader_on_pdf_structure(self, ctx: "ReaderCtx", obj: Dict[str, Any]) -> None:
        try:
            snap = ctx.snap
            refs = obj.get("references") or {}
            entries = refs.get("entries")
            if isinstance(entries, list) and entries:
                snap["references_from_structure"] = [str(x).strip() for x in entries if str(x).strip()]
        except Exception:
            logger.debug("reader_on_pdf_structure_failed", exc_info=True)
