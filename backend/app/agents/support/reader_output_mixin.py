"""ReaderOutputMixin — extracted from PaperAnalysisAgent (see app/agents/paper_analysis_agent.py)."""
from __future__ import annotations

import logging

from typing import Any, Callable, Dict, List, Optional, Tuple
from ..prompts.paper_analysis import ANALYSIS_SYSTEM, READER_CHAT_SYSTEM
from .reader_ctx import ReaderCtx
from ...services.llm.llm_service import is_llm_configured
import re
from .reader_reference_lookup_tool import (
    READER_RECOMMEND_MAX_RESULTS, ReaderReferenceLookupTool,
    READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_REF_BLOCK,
    paper_matches_reader_snap, parse_reader_recommendation_intent,
    prioritize_reader_related_pairs_refs_first, reader_user_allows_external_paper_lookup,
    rerank_reader_pairs_by_anchor_refs_first, resolve_references_via_openalex,
    user_message_may_need_reference_lookup,
    READER_RELATED_FROM_PRE_SEARCH,
)


logger = logging.getLogger(__name__)


class ReaderOutputMixin:
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
        # Match [p3], [p7,p8], [p3, p5], [p3,p5,p7], [p3-p5] (dash ranges) —
        # capture everything between [p and ], split on comma, and parse each
        # item as an int or an N-M range (tolerating an optional 'p' prefix and
        # -, –, — dashes).
        for m in re.finditer(r"\[p([^\]]*)\]", text_out):
            nums: list[int] = []
            for part in m.group(1).split(","):
                part = part.strip().lstrip("p").strip()
                rng = re.match(r"^(\d+)\s*[-–—]\s*p?(\d+)$", part)
                if rng:
                    lo, hi = int(rng.group(1)), int(rng.group(2))
                    if lo <= hi:
                        nums.extend(range(lo, hi + 1))
                    continue
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
