"""ClassificationMixin — extracted from PaperAnalysisAgent (see app/agents/paper_analysis_agent.py)."""
from __future__ import annotations

import logging
import threading

from .task_spec import TaskSpec
from ..prompts.paper_analysis import ANALYSIS_SYSTEM, READER_CHAT_SYSTEM
from app.exceptions import LLMError
from typing import Any, Callable, Dict, List, Optional, Tuple
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
from app.core.paper_paths import normalize_library_category_display
from ...utils import parse_llm_json


logger = logging.getLogger(__name__)

_MAJOR_LOCK = threading.Lock()
_MAJOR_WHITELIST: Optional[Tuple[str, ...]] = None


class ClassificationMixin:
    def _ensure_major_whitelist(self) -> None:
        global _MAJOR_WHITELIST
        if _MAJOR_WHITELIST is not None:
            return
        with _MAJOR_LOCK:
            if _MAJOR_WHITELIST is not None:
                return
            wl: Optional[Tuple[str, ...]] = None
            taxonomy_prompt = (
                "# 任务：为个人/小团队文献库生成顶层大类\n"
                '输出: {"majors":["名称1",...]}\n'
                "- 共 16～24 条；恰含一个「未分类」\n"
                "- 名称须具学术划分意义，覆盖计算机与交叉学科\n"
                "- 每条 2～10 个中文字；互异；禁含「/」及路径非法字符\n"
            )
            try:
                raw = self._llm_chat(ANALYSIS_SYSTEM, taxonomy_prompt)
                parsed = _parse_taxonomy_majors(raw)
                if not parsed:
                    raw = self._llm_chat(ANALYSIS_SYSTEM, taxonomy_prompt + '\n请确保输出合法 JSON。')
                    parsed = _parse_taxonomy_majors(raw)
                if parsed:
                    wl = tuple(parsed)
            except Exception:
                logger.exception("major_taxonomy_bootstrap_failed")
            if not wl:
                raise LLMError("major_taxonomy_bootstrap_failed", code="major_taxonomy_bootstrap_failed")
            _MAJOR_WHITELIST = wl
            logger.info("paper_analysis_major_whitelist_ready", extra={"n": len(wl)})
    def _get_major_whitelist(self) -> Tuple[str, ...]:
        self._ensure_major_whitelist()
        if _MAJOR_WHITELIST is None:
            raise LLMError("major_whitelist_unavailable", code="major_whitelist_unavailable")
        return _MAJOR_WHITELIST
    def _parse_major_json(self, raw: str) -> Optional[str]:
        d = parse_llm_json(raw)
        if not isinstance(d, dict):
            return None
        m = str(d.get("major") or d.get("category") or "").strip()
        if not m:
            return None
        return _nearest_major_in(m, self._get_major_whitelist())
    def _parse_fine_classify_json(self, raw: str, major: str) -> Optional[Tuple[str, List[str]]]:
        d = parse_llm_json(raw)
        if not isinstance(d, dict):
            return None
        cat = normalize_library_category_display(str(d.get("category") or "未分类"))
        tags_raw = d.get("tags")
        extra: List[str] = []
        if isinstance(tags_raw, list):
            for x in tags_raw:
                c = _clean_library_tag(str(x))
                if c:
                    extra.append(c)
            extra = _dedupe_tags(extra)
        if not cat:
            return None
        if major and major != "未分类" and not (cat.startswith(major) or major in cat):
            cat = normalize_library_category_display(f"{major}/{cat.split('/')[-1]}")
        return cat, extra
    def classify_venue_type(self, journal: str | None) -> str | None:
        if not journal or not str(journal).strip():
            return None
        j = str(journal).strip()
        if j.startswith("arXiv:"):
            return "preprint"
        with self._classify_cache_lock:
            if j in self._venue_type_cache:
                return self._venue_type_cache[j]
        try:
            prompt = f'判断以下学术来源名称是会议(conference)还是期刊(journal)。只回复一个单词：conference 或 journal。\n\n名称：{j}'
            resp = self._llm_chat(ANALYSIS_SYSTEM, prompt)
            result = str(resp).strip().lower()
            if "conference" in result:
                vt = "conference"
            elif "journal" in result:
                vt = "journal"
            else:
                vt = None
        except Exception:
            vt = None
        if vt:
            with self._classify_cache_lock:
                self._venue_type_cache[j] = vt
        return vt
    def classify_for_library(
        self,
        title: str,
        abstract: Optional[str],
        journal: Optional[str],
        keywords: Optional[List[str]] = None,
        existing_categories: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        # Cache by content hash so re-save / re-import skips the 2 LLM calls.
        # existing_categories only narrows candidates; same paper → same result,
        # so it is intentionally NOT part of the key.
        cache_key = (
            (title or "").strip().lower(),
            (abstract or "").strip()[:800].lower(),
            (journal or "").strip().lower(),
            tuple(str(k).strip().lower() for k in (keywords or []) if str(k).strip()),
        )
        with self._classify_cache_lock:
            cached = self._classify_cache.get(cache_key)
        if cached is not None:
            return cached[0], list(cached[1])
        result = self._classify_for_library_uncached(
            title, abstract, journal, keywords, existing_categories
        )
        with self._classify_cache_lock:
            self._classify_cache[cache_key] = (result[0], tuple(result[1]))
        return result
    def _classify_for_library_uncached(
        self,
        title: str,
        abstract: Optional[str],
        journal: Optional[str],
        keywords: Optional[List[str]] = None,
        existing_categories: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        kw = "、".join(keywords or []) or "（无）"
        journal = journal or "（无）"
        abstract = (abstract or "").strip() or "（无摘要）"
        seed = f"{title}\n{abstract[:800]}"

        cats_all = [str(x).strip() for x in (existing_categories or []) if str(x).strip()]
        candidates = _top_similar_categories(seed, cats_all, k=18)

        base_user = f"标题：{title}\n摘要：{abstract}\n来源：{journal}\n关键词：{kw}"

        wl = self._get_major_whitelist()
        major_list_block = "\n".join(f"- {m}" for m in wl)
        major_user = f"{base_user}\n\n【可选大类列表】\n{major_list_block}"

        major_fb = "未分类"
        major_user = (
            "# 任务：归类（大类）\n"
            "从【可选大类列表】中选一个最匹配的大类\n"
            '输出: {"major":"大类名"}\n'
            '- 必须从列表选；优先字面匹配，否则语义最接近；无法判断→"未分类"；禁列表外值\n\n'
            + major_user
        )
        major_spec = TaskSpec(
            name="classify_major",
            system_prompt=ANALYSIS_SYSTEM,
            parser=self._parse_major_json,
            max_chars=5200,
        )
        major = self._run_task(major_spec, major_user)
        if not isinstance(major, str) or not major.strip():
            major = major_fb
        major = _nearest_major_in(major, wl)

        if not candidates:
            cat0 = normalize_library_category_display(major)
            self._record_preference_signals(signal="classify", title=title, major=major, category=cat0, shared=True)
            return cat0, []

        prefixed = [c for c in candidates if c.startswith(major) or c.split("/")[0] == major]
        pool = prefixed if len(prefixed) >= 2 else candidates
        pool_block = "\n".join(f"- {c}" for c in pool[:18])

        fine_user = (
            "# 任务：归类（路径与标签）\n"
            "给定大类，从候选已有路径中选择路径，生成标签\n"
            '输出: {"category":"路径","tags":["标签1",...]}\n'
            "- category：优先原样选候选；否则「大类／子类」，子类 2～8 个中文字\n"
            "- tags：3～8 个，单条 ≤24 字，不重复；无法判断可为 []\n"
            "- 禁含路径非法字符 \\ / : * ? \" < > |\n\n"
            f"{base_user}\n\n给定大类：{major}\n\n"
            f"【候选已有路径】（请优先从中复制一条作为 category）\n{pool_block}"
        )
        default_cat = normalize_library_category_display(major)

        def _fine_parser(raw: str) -> Optional[Tuple[str, List[str]]]:
            return self._parse_fine_classify_json(raw, major)

        fine_spec = TaskSpec(
            name="classify_fine",
            system_prompt=ANALYSIS_SYSTEM,
            parser=_fine_parser,
            max_chars=5500,
        )
        out = self._run_task(fine_spec, fine_user)
        if isinstance(out, tuple) and len(out) == 2:
            cat, tags = out[0], out[1]
            cat = normalize_library_category_display(str(cat or "未分类"))
            if isinstance(tags, list):
                cleaned: List[str] = []
                for x in tags:
                    c = _clean_library_tag(str(x))
                    if c:
                        cleaned.append(c)
                tags = _dedupe_tags(cleaned)
            else:
                tags = []
            self._record_preference_signals(signal="classify", title=title, major=major, category=cat, tags=tags, shared=True)
            return cat, tags
        return default_cat, []
