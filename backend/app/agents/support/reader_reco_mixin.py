"""ReaderRecoMixin — extracted from PaperAnalysisAgent (see app/agents/paper_analysis_agent.py)."""
from __future__ import annotations

import logging

from typing import Any, Callable, Dict, List, Optional, Tuple
from .reader_reference_lookup_tool import (
    READER_RECOMMEND_MAX_RESULTS, ReaderReferenceLookupTool,
    READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_REF_BLOCK,
    paper_matches_reader_snap, parse_reader_recommendation_intent,
    prioritize_reader_related_pairs_refs_first, reader_user_allows_external_paper_lookup,
    rerank_reader_pairs_by_anchor_refs_first, resolve_references_via_openalex,
    user_message_may_need_reference_lookup,
    READER_RELATED_FROM_PRE_SEARCH,
)
from .reader_ctx import ReaderCtx
from .paper_analysis_helpers import (
    clip_text as _clip,
    clip_reader_history as _clip_reader_history,
    clean_library_tag as _clean_library_tag,
    dedupe_tags as _dedupe_tags,
    nearest_major_in as _nearest_major_in,
    parse_taxonomy_majors as _parse_taxonomy_majors,
    prioritize_reader_context as _prioritize_reader_context,
    top_similar_categories as _top_similar_categories,
)
from .reader_paper_lookup_tool import ReaderPaperLookupTool, ground_score_paper_vs_reference_blob
from ...services.llm.llm_service import is_llm_configured
import re


logger = logging.getLogger(__name__)


class ReaderRecoMixin:
    def _prune_reco_ref_offset(self) -> None:
        """Bound memory: keep only the most recent entries."""
        d = self._reader_reco_ref_offset
        if len(d) <= self._reco_offset_max_papers:
            return
        # dict preserves insertion order; drop the oldest half.
        keep_from = len(d) // 2
        for k in list(d.keys())[:keep_from]:
            del d[k]
    @staticmethod
    def _dedupe_reader_paper_pairs(buffer: List[Tuple[Any, str]]) -> List[Tuple[Any, str]]:
        seen: set[str] = set()
        out: List[Tuple[Any, str]] = []
        for p, src in buffer:
            k = str(getattr(p, "title", "") or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append((p, src))
        return out
    def _collect_lookup_pairs(self, ctx: ReaderCtx, want_reco: bool) -> List[Tuple[Any, str]]:
        with ctx.lookup_lock:
            raw_pairs = list(ctx.lookup_buffer)
            ctx.lookup_buffer.clear()
        pairs = self._dedupe_reader_paper_pairs(raw_pairs)
        rb_pdf = str(ctx.snap.get("references_section_raw") or "").strip()
        if len(rb_pdf) >= 140 and not (ctx.snap.get("references") or []):
            _thr_bib = 0.48 if want_reco else 0.54
            pairs = [
                (p, s) for p, s in pairs
                if s != READER_RELATED_FROM_BIBLIOGRAPHY
                or ground_score_paper_vs_reference_blob(p, rb_pdf) >= _thr_bib
            ]
        return pairs
    def _reference_fallback_resolve(
        self,
        ctx: ReaderCtx,
        um: str,
        want_reco: bool,
        reco_max: int,
        reco_pid: Optional[int],
    ) -> List[Any]:
        """Resolve related papers from references via OpenAlex when no tool hits."""
        snap = ctx.snap
        extra: List[Any] = []
        if not user_message_may_need_reference_lookup(um):
            return extra
        fb_max = reco_max if want_reco else 2
        resolve_mr = max(fb_max, min(READER_RECOMMEND_MAX_RESULTS, fb_max * 4)) if want_reco else fb_max
        try:
            if snap.get("references"):
                refs_full = [str(x).strip() for x in (snap.get("references") or []) if str(x).strip()]
                off = self._reader_reco_ref_offset.get(reco_pid, 0) if reco_pid else 0
                snap_res: Dict[str, Any] = snap
                if want_reco and reco_pid and refs_full and off >= len(refs_full):
                    off = 0
                    self._reader_reco_ref_offset[reco_pid] = 0
                if want_reco and reco_pid and off > 0 and off < len(refs_full):
                    snap_res = dict(snap)
                    snap_res["references"] = refs_full[off:]
                extra = resolve_references_via_openalex(
                    snap_res,
                    max_results=resolve_mr,
                )
                if extra and want_reco and reco_pid:
                    self._reader_reco_ref_offset[reco_pid] = off + max(1, len(extra))
            elif (snap.get("references_section_raw") or "").strip():
                from ...services.reader.paper_reader_context import reference_strings_for_resolve_fallback

                ref_lines = reference_strings_for_resolve_fallback(
                    str(snap.get("references_section_raw") or "")
                )
                rs_struct = snap.get("references_from_structure")
                if isinstance(rs_struct, list) and rs_struct:
                    ref_lines = [str(x).strip() for x in rs_struct if str(x).strip()] or ref_lines
                ref_core = list(ref_lines)
                if ref_core and is_llm_configured():
                    try:
                        from ...services.reader.reader_recommend_llm import merge_ref_lines_with_llm_queries

                        merged = merge_ref_lines_with_llm_queries(
                            str(snap.get("references_section_raw") or ""),
                            snap,
                            ref_core,
                            max_queries=12,
                        )
                        ref_lines = merged if merged else ref_core
                    except Exception:
                        logger.debug("merge_llm_ref_queries_failed", exc_info=True)
                        ref_lines = ref_core
                else:
                    ref_lines = ref_core
                if ref_lines:
                    off = self._reader_reco_ref_offset.get(reco_pid, 0) if reco_pid else 0
                    if want_reco and reco_pid and off >= len(ref_lines):
                        off = 0
                        self._reader_reco_ref_offset[reco_pid] = 0
                    if want_reco and reco_pid and off > 0:
                        ref_lines = ref_lines[off:]
                    if ref_lines:
                        snap_fb = dict(snap)
                        snap_fb["references"] = ref_lines
                        extra = resolve_references_via_openalex(
                            snap_fb,
                            max_results=resolve_mr,
                        )
                        rb = str(snap.get("references_section_raw") or "").strip()
                        if extra and len(rb) >= 140 and not (snap.get("references") or []):
                            raw_extra = list(extra)

                            def _gf(th: float) -> List[Any]:
                                return [
                                    p
                                    for p in raw_extra
                                    if ground_score_paper_vs_reference_blob(p, rb) >= th
                                ]

                            extra = _gf(0.54)
                            if not extra and want_reco:
                                extra = _gf(0.42)
                            if not extra and want_reco and raw_extra:
                                extra = list(raw_extra)[: max(1, min(len(raw_extra), fb_max))]
                        if extra and want_reco and reco_pid:
                            self._reader_reco_ref_offset[reco_pid] = off + max(1, len(extra))
        except Exception:
            logger.debug("reader_reference_server_fallback_failed", exc_info=True)
        return extra
    def _filter_and_rerank_pairs(
        self,
        ctx: ReaderCtx,
        pairs: List[Tuple[Any, str]],
        um: str,
        want_reco: bool,
        reco_max: int,
        history_lines: str,
    ) -> Tuple[List[Any], List[str]]:
        snap = ctx.snap
        hist = _clip_reader_history((history_lines or "").strip() or "（尚无此前对话）", max_chars=2200)
        bib_only = (
            (want_reco or user_message_may_need_reference_lookup(um))
            and not reader_user_allows_external_paper_lookup(um)
        )
        pairs = [(p, s) for p, s in pairs if not paper_matches_reader_snap(snap, p)]
        if bib_only:
            pairs = [
                (p, s)
                for p, s in pairs
                if s in (READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_REF_BLOCK, READER_RELATED_FROM_PRE_SEARCH)
            ]
        if want_reco and pairs:
            try:
                if is_llm_configured():
                    from ...services.reader.reader_recommend_llm import rerank_reader_recommend_pairs_by_llm

                    pairs = rerank_reader_recommend_pairs_by_llm(
                        snap,
                        pairs,
                        user_message=um,
                        history_lines=hist,
                        reco_max_hint=reco_max,
                    )
                else:
                    pairs = rerank_reader_pairs_by_anchor_refs_first(snap, pairs, k=reco_max)
            except Exception:
                logger.debug("reader_llm_recommend_rerank_failed", exc_info=True)
                pairs = rerank_reader_pairs_by_anchor_refs_first(snap, pairs, k=reco_max)
        if pairs:
            pairs = prioritize_reader_related_pairs_refs_first(pairs)
        if want_reco and pairs:
            cap = max(1, min(int(reco_max or 2), READER_RECOMMEND_MAX_RESULTS, len(pairs)))
            pairs = pairs[:cap]
        papers = [p for p, _ in pairs]
        provenances = [s for _, s in pairs]
        return papers, provenances
    def _extract_llm_recommended_papers(
        self, ctx: ReaderCtx, text_out: str, papers: List[Any], provenances: List[str]
    ) -> Tuple[List[Any], List[str]]:
        if not (text_out and len(text_out) >= 80):
            return papers, provenances
        snap = ctx.snap
        try:
            prompt = (
                "从以下学术助手的回复中，提取被推荐的论文信息。\n"
                "返回纯 JSON 数组，每项可含 title（英文题名）和/或 arxiv_id（如 2307.05973）。\n"
                '格式：[{"title": "...", "arxiv_id": "..."}, ...]\n'
                "若回复未推荐具体论文，返回 []。\n\n"
                "回复原文：\n" + text_out[:3000]
            )
            llm_text = self._llm_chat("", prompt)
            import json as _json
            extracted = _json.loads(llm_text.strip().removeprefix("```json").removesuffix("```").strip())
            if isinstance(extracted, list) and extracted:
                logger.info("reader_llm_extract: got %d papers from LLM", len(extracted))
                try:
                    from app.api.dependencies import get_searcher as _es
                    from app.services.papers.papers_converters import litpaper_to_api_paper as _ep
                    ese = _es()
                    existing_titles = {str(getattr(p, "title", "") or "").strip().lower() for p in papers}
                    existing_titles.add(str(snap.get("title") or "").strip().lower())
                    for item in extracted[:6]:
                        if not isinstance(item, dict):
                            continue
                        title = str(item.get("title") or "").strip()
                        axid = str(item.get("arxiv_id") or "").strip()

                        if axid and re.match(r"^\d{4}\.\d{4,5}", axid):
                            try:
                                for fp in (ese.search_arxiv("", max_results=2, arxiv_id_list=axid,
                                        http_timeout_sec=8, http_max_attempts=1) or []):
                                    afp = _ep(fp)
                                    tafp = str(getattr(afp, "title", "") or "").strip().lower()
                                    if tafp and tafp not in existing_titles:
                                        existing_titles.add(tafp)
                                        papers.append(afp)
                                        provenances.append(READER_RELATED_FROM_PRE_SEARCH)
                                        logger.info("reader_llm_extract: added by arxiv %s", axid)
                            except (RuntimeError, ConnectionError, TimeoutError):
                                continue
                            except Exception as exc:
                                logger.debug("reader_llm_extract_arxiv_unexpected: %s", exc)
                                continue

                        if title and len(title) >= 4:
                            try:
                                for fp in (ese.search_openalex(title, max_results=2, venue_proceedings_journal=False) or []):
                                    afp = _ep(fp)
                                    tafp = str(getattr(afp, "title", "") or "").strip().lower()
                                    if tafp and tafp not in existing_titles:
                                        existing_titles.add(tafp)
                                        papers.append(afp)
                                        provenances.append(READER_RELATED_FROM_PRE_SEARCH)
                                        logger.info("reader_llm_extract: added by title %s", tafp[:80])
                            except (RuntimeError, ConnectionError, TimeoutError):
                                continue
                            except Exception as exc:
                                logger.debug("reader_llm_extract_openalex_unexpected: %s", exc)
                                continue
                except (RuntimeError, ConnectionError, TimeoutError) as e:
                    logger.warning("reader_llm_extract_search_failed: %s", e)
                except Exception as e:
                    logger.warning("reader_llm_extract_search_unexpected: %s", e)
        except (TypeError, ValueError) as e:
            logger.warning("reader_llm_extract_parse_failed: %s", e)
        except Exception as e:
            logger.warning("reader_llm_extract_unexpected: %s", e)
        return papers, provenances
