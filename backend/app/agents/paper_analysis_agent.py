
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.paper_paths import normalize_library_category_display

from ..utils import parse_llm_json
from ..services.llm.llm_service import is_llm_configured
from ..services.llm.agent_loop import run_agent_loop_sync, ToolSpec
from .base import BaseAgent
from .support.paper_analysis_helpers import (
    clip_text as _clip,
    clip_reader_history as _clip_reader_history,
    clean_library_tag as _clean_library_tag,
    dedupe_tags as _dedupe_tags,
    nearest_major_in as _nearest_major_in,
    parse_taxonomy_majors as _parse_taxonomy_majors,
    prioritize_reader_context as _prioritize_reader_context,
    top_similar_categories as _top_similar_categories,
)
from .support.reader_pdf_parse_tool import ReaderPdfParseTool
from .support.reader_table_tool import ReaderTableTool
from .support.reader_ctx import ReaderCtx
from .support.reader_paper_lookup_tool import ReaderPaperLookupTool, ground_score_paper_vs_reference_blob
from .support.reader_reference_lookup_tool import (
    READER_RECOMMEND_MAX_RESULTS, ReaderReferenceLookupTool,
    READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_REF_BLOCK,
    paper_matches_reader_snap, parse_reader_recommendation_intent,
    prioritize_reader_related_pairs_refs_first, reader_user_allows_external_paper_lookup,
    rerank_reader_pairs_by_anchor_refs_first, resolve_references_via_openalex,
    strip_reader_reco_boilerplate, user_message_may_need_reference_lookup,
    READER_RELATED_FROM_PRE_SEARCH,
)

from .prompts.paper_analysis import ANALYSIS_SYSTEM, READER_CHAT_SYSTEM

logger = logging.getLogger(__name__)

def _reader_resolve_user_hint(user_message: str, snap: Dict[str, Any], *, want_reco: bool) -> str:
    um = (user_message or "").strip()
    if not want_reco:
        return um[:900]
    core = strip_reader_reco_boilerplate(um) or um
    if len(core) >= 28:
        return um[:900]
    title = str(snap.get("title") or "").strip()
    ab = str(snap.get("abstract") or "").strip()[:520]
    bits = [x for x in (core, title, ab) if x]
    return "\n".join(bits).strip()[:900] or um[:900]

_MAJOR_LOCK = threading.Lock()
_MAJOR_WHITELIST: Optional[Tuple[str, ...]] = None

@dataclass
class TaskSpec:
    name: str
    system_prompt: str
    parser: Optional[Callable[[str], Any]] = None
    max_chars: int = 6000

class PaperAnalysisAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__()
        # Per-paper recommendation pagination — intentionally on the singleton
        # so recommendations don't repeat across requests for the same paper.
        self._reader_reco_ref_offset: Dict[int, int] = {}
        self._reco_offset_max_papers = 200
        # Venue-type cache is read-only after populate; safe to share.
        self._venue_type_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 无状态 LLM 调用 —— 替代原 SimpleAgent 工厂。
    #
    # 历史问题：SimpleAgent.run() 往自身 _history 累积并在每次调用重建 messages，
    # 跨并发请求（paper_reader_reply 跑在线程池）会交叉污染 history；clear_history()
    # 在并发下有 TOCTOU 窗口，故原方案每请求新建 SimpleAgent。
    # 现方案：_llm_chat / run_agent_loop_sync 完全无状态（history 由 caller 传入，
    # 默认空），self.llm（LLMClient）复用但不持可变状态 —— 并发隔离由架构保证。
    # ------------------------------------------------------------------
    def _build_reader_tools(self, ctx: "ReaderCtx") -> list[ToolSpec]:
        """构造 reader 工具的 ToolSpec 列表（无状态函数式工具）。

        _to_spec 使用工具的 to_openai_schema() 生成 JSON schema，
        并按原 SimpleAgent._execute_tool_call 的语义把工具结果转成字符串
        （ERROR/PARTIAL 加前缀）。
        """

        def _to_spec(tool: Any) -> ToolSpec:
            schema = tool.to_openai_schema()["function"]

            def fn(args: dict[str, Any]) -> str:
                resp = tool.run_with_timing(args)
                status = getattr(resp, "status", "SUCCESS")
                if status == "ERROR":
                    code = (getattr(resp, "error_info", None) or {}).get("code", "UNKNOWN")
                    return f"❌ 错误 [{code}]: {resp.text}"
                if status == "PARTIAL":
                    return f"⚠️ 部分成功: {resp.text}"
                return resp.text

            return ToolSpec(
                name=schema["name"],
                description=schema["description"],
                parameters_schema=schema["parameters"],
                fn=fn,
            )

        return [
            _to_spec(ReaderPaperLookupTool(
                on_papers_found=lambda papers, src: self._reader_tool_on_found(ctx, papers, src),
                get_snap=lambda: ctx.snap,
            )),
            _to_spec(ReaderReferenceLookupTool(
                get_snap=lambda: ctx.snap,
                on_papers_found=lambda papers, src: self._reader_tool_on_found(ctx, papers, src),
                get_user_message=lambda: ctx.user_message,
            )),
            _to_spec(ReaderPdfParseTool(
                get_snap=lambda: ctx.snap,
                on_parsed=lambda obj: self._reader_on_pdf_structure(ctx, obj),
            )),
            _to_spec(ReaderTableTool(get_snap=lambda: ctx.snap)),
        ]

    def _llm_chat(self, system_prompt: str, user_prompt: str) -> str:
        """无状态单轮 LLM 调用（替代 _new_*_agent().run）。"""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return self.llm.chat(messages).content

    def _prune_reco_ref_offset(self) -> None:
        """Bound memory: keep only the most recent entries."""
        d = self._reader_reco_ref_offset
        if len(d) <= self._reco_offset_max_papers:
            return
        # dict preserves insertion order; drop the oldest half.
        keep_from = len(d) // 2
        for k in list(d.keys())[:keep_from]:
            del d[k]

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
                    self._llm_chat(ANALYSIS_SYSTEM, taxonomy_prompt + '\n请确保输出合法 JSON。')
                if parsed:
                    wl = tuple(parsed)
            except Exception:
                logger.exception("major_taxonomy_bootstrap_failed")
            if not wl:
                raise RuntimeError("major_taxonomy_bootstrap_failed")
            _MAJOR_WHITELIST = wl
            logger.info("paper_analysis_major_whitelist_ready", extra={"n": len(wl)})

    def _get_major_whitelist(self) -> Tuple[str, ...]:
        self._ensure_major_whitelist()
        if _MAJOR_WHITELIST is None:
            raise RuntimeError("major_whitelist_unavailable")
        return _MAJOR_WHITELIST

    def _cleanup_mixed_reader_response(self, reply: str, user_message: str) -> str:
        """Use one cleanup pass when tool output leaks into the final reply."""
        t = reply.strip()
        # Typical leak: disclaimer plus useful data, or raw tool JSON.
        has_disclaimer = any(x in t[:300] for x in ("材料不足", "缺少", "仅有标题")) or bool(re.search(r"基于.*推测", t[:300]))
        has_actual_data = len(t) > 600 and any(x in t for x in ("Tab.", "Table", "结果", "实验", "| "))
        has_tool_artifact = "reader_pdf_struct" in t or '"chapters"' in t[:500]

        if (has_disclaimer and has_actual_data) or has_tool_artifact:
            if not is_llm_configured():
                return reply
            logger.info("paper_reader: detected mixed response, running cleanup pass")
            try:
                um = (user_message or "").strip()[:200]
                # Keep extracted paper facts; drop wrapper noise.
                cleaned = re.sub(r"^.*?(?:当前文献材料说明|当前提供的材料).*?\n\n", "", t, flags=re.S)
                cleaned = re.sub(r"reader_pdf_struct\S*", "", cleaned)
                cleaned = cleaned.strip()[:6000]
                if cleaned:
                    prompt = (
                        f"用户问：{um}\n\n"
                        f"以下是从论文中提取的信息（可能含多个片段）：\n\n{cleaned}\n\n"
                        "请整合成一个连贯的回答。用中文分点说明。如有表格数据用 Markdown 表格呈现。"
                        "不要提及'材料不足'或'推测'——只基于已有信息回答，不确定的地方标注'论文未提供'。"
                    )
                    result = self._reader_chat_llm(prompt)
                    if result.strip():
                        return result.strip()
            except Exception:
                logger.debug("cleanup_mixed_response_failed", exc_info=True)
        return reply

    @staticmethod
    def _looks_like_raw_tool_output(text: str) -> bool:
        """Detect raw tool output that still needs interpretation."""
        t = (text or "").strip()
        if not t:
            return False
        if "reader_pdf_structure" in t:
            return True
        if t.startswith("## ") and len(t) > 300:
            first_line = t.split("\n")[0]
            if re.match(r"^## \d", first_line) or re.match(r"^## [A-Z]", first_line):
                return True
        if '"chapters"' in t[:500] or '"references"' in t[:500]:
            return True
        return False

    def _interpret_tool_output(self, tool_output: str, user_message: str) -> str:
        """Convert raw tool output into a user-facing answer."""
        if not is_llm_configured():
            return tool_output
        try:
            um = (user_message or "").strip()[:200]
            # Strip tool wrappers before asking the LLM to explain.
            cleaned = re.sub(r"^.*?reader_pdf_structure[：:]\s*", "", tool_output, flags=re.S)
            cleaned = re.sub(r"^以下为 JSON.*?\n", "", cleaned)
            cleaned = re.sub(r"^\s*\{\s*\"chapters\".*?\n", "", cleaned)
            cleaned = re.sub(r"reader_pdf_structu\S*$", "", cleaned)
            cleaned = re.sub(r"[\u007F-\u009F]", "", cleaned)
            cleaned = cleaned.strip()
            if not cleaned or len(cleaned) < 50:
                return "当前文献材料不足以回答该问题。PDF 文本提取不完整，建议确认 PDF 文件是否可读。"
            cleaned = cleaned[:6000]
            prompt = (
                f"用户问：{um}\n\n"
                f"以下是从论文中提取的相关章节内容：\n\n{cleaned}\n\n"
                "请用中文为用户解读这段内容。分点说明关键发现、方法和结论。"
                "用 Markdown 表格对比数据（如有）。\n"
                "重要：如果上面的内容不足以回答用户问题（如仅有章节标题无正文），"
                "请直接说明材料不足，严禁编造论文中不存在的数据、方法或结论。"
            )
            raw = self._reader_chat_llm(prompt)
            return raw.strip() or tool_output
        except Exception:
            logger.debug("interpret_tool_output_failed", exc_info=True)
            return tool_output

    def _reader_chat_llm(self, prompt: str) -> str:
        """Reader chat without tools (interpreter pass)."""
        return self._llm_chat(READER_CHAT_SYSTEM, prompt)

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
    def _run_task(self, spec: TaskSpec, user: str) -> Any:
        prompt = _clip(user, spec.max_chars)
        try:
            raw = self._llm_chat(spec.system_prompt, prompt)
        except Exception as exc:
            logger.exception("paper_analysis_llm_failed", extra={"task": spec.name})
            raise RuntimeError(f"paper_analysis_llm_failed:{spec.name}") from exc

        raw_text = (raw if isinstance(raw, str) else str(raw or "")).strip()

        if not spec.parser:
            if not raw_text:
                if spec.name == "paper_reader_reply":
                    return ""
                raise RuntimeError(f"paper_analysis_empty_response:{spec.name}")

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
        raise RuntimeError(f"paper_analysis_parse_failed:{spec.name}")

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
        out = run_agent_loop_sync(
            llm=self.llm,
            system_prompt=READER_CHAT_SYSTEM,
            history=[],
            user_prompt=user,
            tools=self._build_reader_tools(ctx),
            max_tool_iterations=5,
            temperature=0.3,
        )
        if isinstance(out, str) and out.strip():
            if self._looks_like_raw_tool_output(out):
                logger.info("paper_reader: detected raw tool output, invoking interpreter")
                out = self._interpret_tool_output(out, user)
            out = self._cleanup_mixed_reader_response(out, um)
        return out if isinstance(out, str) else ("" if out is None else str(out))

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
                    user_hint=_reader_resolve_user_hint(um, snap, want_reco=want_reco),
                )
                if extra and want_reco and reco_pid:
                    self._reader_reco_ref_offset[reco_pid] = off + max(1, len(extra))
            elif (snap.get("references_section_raw") or "").strip():
                from ..services.reader.paper_reader_context import reference_strings_for_resolve_fallback

                ref_lines = reference_strings_for_resolve_fallback(
                    str(snap.get("references_section_raw") or "")
                )
                rs_struct = snap.get("references_from_structure")
                if isinstance(rs_struct, list) and rs_struct:
                    ref_lines = [str(x).strip() for x in rs_struct if str(x).strip()] or ref_lines
                ref_core = list(ref_lines)
                if ref_core and is_llm_configured():
                    try:
                        from ..services.reader.reader_recommend_llm import merge_ref_lines_with_llm_queries

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
                            user_hint=_reader_resolve_user_hint(um, snap, want_reco=want_reco),
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
                    from ..services.reader.reader_recommend_llm import rerank_reader_recommend_pairs_by_llm

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

    def _build_text_output(self, ctx: ReaderCtx, out: str, papers: List[Any], um: str) -> str:
        snap = ctx.snap
        text_out = (str(out) if out is not None else "").strip()
        if text_out:
            return text_out
        if papers:
            if snap.get("references_source") == "pdf_section":
                return (
                    "已从 PDF 参考文献区检索到相关论文，点击下方「推荐论文」查看。\n\n"
                    "你可以继续问：这些方法有什么区别？或指定某篇深入分析。"
                )
            return (
                "已根据参考文献检索到相关论文，点击下方「推荐论文」查看。\n\n"
                    "你可以继续问：这些方法有什么区别？或指定某篇深入分析。"
            )
        if user_message_may_need_reference_lookup(um) and not (snap.get("references") or []) and not (
            snap.get("references_section_raw") or ""
        ).strip():
            return (
                "这篇论文暂时没有可用的参考文献数据。\n\n"
                "可能原因：PDF 为扫描版、参考文献区未被正确提取，或论文本身没有参考文献。\n\n"
                "你可以：\n"
                "- 直接粘贴一条英文论文标题或 DOI，我来帮你检索\n"
                "- 重新保存该论文（换个数据源）以重试 PDF 解析\n"
                "- 基于已有摘要提问"
            )
        if user_message_may_need_reference_lookup(um) and (snap.get("references_section_raw") or "").strip():
            return (
                "已找到 PDF 中的参考文献区，但本轮检索未匹配到具体论文。\n\n"
                "你可以：指定一条英文题名或 DOI，我帮你精确检索。"
            )
        if user_message_may_need_reference_lookup(um):
            return (
                "已尝试在 OpenAlex / arXiv / DBLP 中检索参考文献，但未找到足够匹配的论文。\n\n"
                "你可以：粘贴 DOI 或英文题名，我用检索工具帮你找。"
            )
        return "抱歉，本次未能生成有效回复。请尝试换一种方式提问，或稍后重试。"

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
            llm_text = self.llm.chat([{"role": "user", "content": prompt}]).content
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

    def _parse_citations_from_reply(self, text_out: str, ctx: ReaderCtx) -> List[Dict[str, Any]]:
        """Extract [pN] page anchors from the reply. Soft constraint — LLM may omit."""
        if not text_out:
            return []
        pages = ctx.snap.get("_pdf_pages")
        valid_pages: set[int] = set()
        if isinstance(pages, list):
            for pg in pages:
                try:
                    valid_pages.add(int(pg.get("page")))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        citations: List[Dict[str, Any]] = []
        seen: set[int] = set()
        # Match [p3], [p7,p8], [p3, p5], [p3,p5,p7] — capture everything between
        # [p and ], then split on comma and parse each as an int (tolerating an
        # optional 'p' prefix on each page number).
        for m in re.finditer(r"\[p([^\]]*)\]", text_out):
            nums: list[int] = []
            for part in m.group(1).split(","):
                part = part.strip().lstrip("p").strip()
                if part.isdigit():
                    nums.append(int(part))
            for n in nums:
                if valid_pages and n not in valid_pages:
                    continue  # filter out-of-range page anchors
                if n in seen:
                    continue
                seen.add(n)
                start = max(0, m.start() - 120)
                end = min(len(text_out), m.end() + 120)
                snippet = text_out[start:end].strip().replace("\n", " ")
                if len(snippet) > 240:
                    snippet = snippet[:240] + "…"
                citations.append({"marker": f"[p{n}]", "page": n, "snippet": snippet})
        return citations

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

