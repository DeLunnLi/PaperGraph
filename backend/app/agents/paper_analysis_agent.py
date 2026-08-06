
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from app.exceptions import LLMError

from .base import BaseAgent
from .support.paper_analysis_helpers import (
    clip_text as _clip,
    clip_reader_history as _clip_reader_history,
    prioritize_reader_context as _prioritize_reader_context,
)
from .support.reader_ctx import ReaderCtx
from .support.reader_tools_mixin import ReaderToolsMixin
from .support.reader_reco_mixin import ReaderRecoMixin
from .support.reader_output_mixin import ReaderOutputMixin
from .support.classification_mixin import ClassificationMixin
from .support.task_spec import TaskSpec
from .support.reader_reference_lookup_tool import (
    READER_RELATED_FROM_BIBLIOGRAPHY,
    parse_reader_recommendation_intent,
)

logger = logging.getLogger(__name__)



class PaperAnalysisAgent(
    ReaderToolsMixin, ReaderRecoMixin, ReaderOutputMixin, ClassificationMixin, BaseAgent,
):
    def __init__(self) -> None:
        super().__init__()
        # Per-paper recommendation pagination — intentionally on the singleton
        # so recommendations don't repeat across requests for the same paper.
        self._reader_reco_ref_offset: Dict[int, int] = {}
        self._reco_offset_max_papers = 200
        # Venue-type cache is read-only after populate; safe to share.
        self._venue_type_cache: dict[str, str] = {}
        # (title, abstract, journal, keywords) -> (category, tags) cache so
        # re-saving / re-importing an already-classified paper skips the 2 LLM
        # round-trips. Process-wide singleton, guarded by a lock.
        self._classify_cache: TTLCache = TTLCache(maxsize=1024, ttl=7 * 86400)
        self._classify_cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # LLM 调用 —— hello-agents 原语复用。
    #
    # Reader 工具循环用 SimpleAgent（每请求新建，避免单例 _history 跨并发请求污染）；
    # 工具注册到 ToolRegistry，SimpleAgent 自带 function-calling 多轮 + run_with_timing。
    # 其余单轮调用（classify / taxonomy / interpreter / cleanup）走无状态 llm.invoke。
    # self.llm 为 HelloAgentsLLM 单例，无可变调用状态 —— 并发隔离由 ReaderCtx +
    # 每请求 SimpleAgent 保证。
    # ------------------------------------------------------------------
    def _llm_chat(self, system_prompt: str, user_prompt: str) -> str:
        """无状态单轮 LLM 调用（HelloAgentsLLM.invoke 返回 LLMResponse，取 .content）。"""
        from ..services.llm.agent_runtime import stateless_llm_chat

        return stateless_llm_chat(self.llm, system_prompt, user_prompt)

    def _run_task(self, spec: TaskSpec, user: str) -> Any:
        prompt = _clip(user, spec.max_chars)
        try:
            raw = self._llm_chat(spec.system_prompt, prompt)
        except Exception as exc:
            logger.exception("paper_analysis_llm_failed", extra={"task": spec.name})
            raise LLMError(f"paper_analysis_llm_failed:{spec.name}",
                           code="paper_analysis_llm_failed") from exc

        raw_text = (raw if isinstance(raw, str) else str(raw or "")).strip()

        if not spec.parser:
            if not raw_text:
                if spec.name == "paper_reader_reply":
                    return ""
                raise LLMError(f"paper_analysis_empty_response:{spec.name}",
                               code="paper_analysis_empty_response")

            # Some tool responses need a final explanation pass.
            if spec.name == "paper_reader_reply" and self._looks_like_raw_tool_output(raw_text):
                logger.info("paper_reader: detected raw tool output, invoking interpreter")
                raw_text = self._interpret_tool_output(raw_text, user)

            return raw_text

        data = spec.parser(raw_text)
        if data is not None:
            return data

        try:
            raw2 = self._llm_chat(spec.system_prompt, "请只输出合法 JSON。\n" + prompt)
            data2 = spec.parser((raw2 or "").strip())
            if data2 is not None:
                return data2
        except Exception:
            logger.exception("paper_analysis_json_retry_failed", extra={"task": spec.name})
        raise LLMError(f"paper_analysis_parse_failed:{spec.name}",
                       code="paper_analysis_parse_failed")

    def _record_preference_signals(
        self,
        *,
        signal: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        category: Optional[str] = None,
        major: Optional[str] = None,
        shared: bool = True,
    ) -> None:
        try:
            from ..services.memory.agent_memory import get_agent_memory

            am = get_agent_memory()
            parts: List[str] = [f"偏好信号({signal})"]
            if title:
                parts.append(f"title={str(title).strip()[:120]}")
            if major:
                parts.append(f"major={str(major).strip()[:24]}")
            if category:
                parts.append(f"cat={str(category).strip()[:40]}")
            if tags:
                clean = [str(x).strip() for x in (tags or []) if str(x).strip()][:10]
                if clean:
                    parts.append("tags=" + ",".join(clean))
            line = " | ".join(parts)[:360]
            am.add(agent_name="paper_analysis", content=line, memory_type="working", importance=0.55, shared=bool(shared))
        except Exception:
            return

    # ------------------------------------------------------------------
    # paper_reader_reply — split into per-responsibility helpers below.
    # All mutable per-request state lives in ctx (ReaderCtx). Only
    # _reader_reco_ref_offset stays on the singleton (cross-request
    # recommendation pagination).
    # ------------------------------------------------------------------
    def _init_reader_ctx(
        self, reader_snap: Optional[Dict[str, Any]], user_message: str
    ) -> Tuple[ReaderCtx, Optional[int], str, bool, int]:
        snap: Dict[str, Any] = dict(reader_snap or {})
        ctx = ReaderCtx(snap=snap)
        reco_pid: Optional[int] = None
        try:
            spid = snap.get("paper_id")
            if spid is not None and int(spid) > 0:
                reco_pid = int(spid)
        except (TypeError, ValueError):
            reco_pid = None
        um = _clip(user_message, 900)
        ctx.user_message = um
        want_reco, reco_max = parse_reader_recommendation_intent(um)
        return ctx, reco_pid, um, want_reco, reco_max

    def _run_reader_llm(self, ctx: ReaderCtx, context_block: str, history_lines: str, um: str) -> str:
        prioritized_ctx = _prioritize_reader_context(context_block, max_chars=3600)
        hist = _clip_reader_history((history_lines or "").strip() or "（尚无此前对话）", max_chars=2200)
        try:
            from ..services.memory.agent_memory import get_agent_memory

            # Reader agent wants insights from: reader (past Q&A), search (what user searched), deep_search (synthesis)
            mem_block = get_agent_memory().build_context_block(
                agent_name="paper_analysis", query=um,
                tags=["reader", "search", "deep_search", "kg"],
            )
        except Exception:
            mem_block = ""
        # Limit memory block size — give global memory more room (was 800, now 1500)
        if mem_block and len(mem_block) > 1500:
            mem_block = mem_block[:1500] + "…"
        user = (
            (f"【共享/独立记忆】\n{mem_block}\n\n" if mem_block else "")
            + f"【当前文献材料】\n{prioritized_ctx}\n\n"
            + f"【对话历史】\n{hist}\n\n"
            + f"【用户最新问题】\n{um}"
        )
        user = _clip(user, 7200)
        out = self._new_reader_agent(ctx).run(user)
        if isinstance(out, str) and out.strip():
            if self._looks_like_raw_tool_output(out):
                logger.info("paper_reader: detected raw tool output, invoking interpreter")
                out = self._interpret_tool_output(out, user)
            out = self._cleanup_mixed_reader_response(out, um)
        return out if isinstance(out, str) else ("" if out is None else str(out))

    def _record_reader_memory(self, ctx: ReaderCtx, um: str, text_out: str) -> None:
        try:
            from ..services.memory.agent_memory import get_agent_memory

            am = get_agent_memory()
            # Paper-specific memory (not shared)
            am.add(agent_name="paper_analysis", content=f"用户问：{um}", memory_type="working", importance=0.55, shared=False)
            am.add(agent_name="paper_analysis", content=f"助手答：{text_out[:240]}", memory_type="working", importance=0.5, shared=False)
            # Shared cross-paper insights — tagged "reader" so search/daily agents can find them
            snap = ctx.snap
            paper_title = str(snap.get("title") or "")[:80]
            shared_content = f"[{paper_title}] 问:{um[:60]} → 要点:{text_out[:160]}"
            am.add(agent_name="paper_analysis", content=shared_content, memory_type="working",
                   importance=0.6, shared=True, tags=["reader"])
            # If the answer is substantive (>200 chars), also store as episodic for long-term recall
            if len(text_out) > 200:
                episodic_content = f"[{paper_title}] {text_out[:300]}"
                am.add(agent_name="paper_analysis", content=episodic_content, memory_type="episodic",
                       importance=0.65, shared=True, tags=["reader"])
        except Exception:
            pass

    def paper_reader_reply(
        self,
        context_block: str,
        history_lines: str,
        user_message: str,
        reader_snap: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, List[Any], List[str], List[Dict[str, Any]]]:
        ctx, reco_pid, um, want_reco, reco_max = self._init_reader_ctx(reader_snap, user_message)
        self._prune_reco_ref_offset()
        try:
            out = self._run_reader_llm(ctx, context_block, history_lines, um)
            pairs = self._collect_lookup_pairs(ctx, want_reco)
            if not pairs:
                extra = self._reference_fallback_resolve(ctx, um, want_reco, reco_max, reco_pid)
                if extra:
                    pairs = [(p, READER_RELATED_FROM_BIBLIOGRAPHY) for p in extra]
            papers, provenances = self._filter_and_rerank_pairs(
                ctx, pairs, um, want_reco, reco_max, history_lines
            )
            text_out = self._build_text_output(ctx, out, papers, um)
            self._record_reader_memory(ctx, um, text_out)
            papers, provenances = self._extract_llm_recommended_papers(ctx, text_out, papers, provenances)
            citations = self._parse_citations_from_reply(text_out, ctx)
            logger.info(
                "reader_post: text_out_len=%d papers=%d want_reco=%s citations=%d",
                len(text_out or ""), len(papers), want_reco, len(citations),
            )
            return (text_out, papers, provenances, citations)
        finally:
            # ctx holds all per-request state; nothing to clear on the singleton.
            pass

