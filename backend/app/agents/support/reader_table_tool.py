from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ReaderTableTool:
    """Extract a specific table from the current paper's PDF - no longer inherits from hello_agents.Tool."""

    def __init__(self, *, get_snap: Callable[[], dict[str, Any]]) -> None:
        self._get_snap = get_snap
        self.name = "reader_pdf_table"
        self.description = (
            "获取当前论文 PDF 中的指定表格内容。当用户询问表格数据或论文提到 'Tab. 3'/'Table 4' 时调用。"
            "输入表号（如 '3'、'4'）或关键词（如 'ImageNet'、'ablation'），返回对应表格的 Markdown 内容。"
        )

    def to_openai_schema(self) -> dict[str, Any]:
        """Generate OpenAI function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_ref": {
                            "type": "string",
                            "description": "表格编号（如 '3'）或关键词（如 'ImageNet'、'ablation'）",
                        },
                    },
                    "required": ["table_ref"],
                },
            },
        }

    def run_with_timing(self, parameters: dict[str, Any]) -> Any:
        """Run tool and return a response object compatible with old ToolResponse format."""
        start = time.time()
        try:
            result = self.run(parameters)
            elapsed_ms = int((time.time() - start) * 1000)
            return _ToolResult("SUCCESS", result, elapsed_ms)
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.debug("reader_pdf_table_failed", exc_info=exc)
            return _ToolResult("ERROR", f"reader_pdf_table：执行失败：{exc}", elapsed_ms, code="EXEC_FAILED")

    def run(self, parameters: dict[str, Any]) -> str:
        ref = str(parameters.get("table_ref") or "").strip()
        if not ref:
            return "请指定表格编号或关键词，如 table_ref='3'"

        try:
            snap = self._get_snap() or {}
        except Exception as exc:
            return f"读取快照失败：{exc}"

        pdf_path = str(snap.get("_pdf_abspath") or "").strip()
        if not pdf_path:
            merged = str(snap.get("_pdf_merged_for_structure") or "").strip()
            if not merged or len(merged) < 200:
                return "当前文献无可用 PDF。请先确认论文已保存且 PDF 已下载。"
            tables = self._extract_tables_from_text(merged)
        else:
            try:
                from ...services.reader.paper_reader_context import extract_pdf_tables_markdown
                tables_md = extract_pdf_tables_markdown(pdf_path)
                if tables_md:
                    tables = self._parse_table_blocks(tables_md)
                else:
                    merged = str(snap.get("_pdf_merged_for_structure") or "").strip()
                    tables = self._extract_tables_from_text(merged) if merged else []
            except Exception:
                merged = str(snap.get("_pdf_merged_for_structure") or "").strip()
                tables = self._extract_tables_from_text(merged) if merged else []

        if not tables:
            return "未能从 PDF 中提取到表格。表格可能为图片格式或 PDF 文本提取不完整。"

        matched = self._find_table(tables, ref)
        if not matched:
            available = [t.get("label", f"表{i+1}") for i, t in enumerate(tables[:8])]
            return f"未找到匹配 '{ref}' 的表格。可用表格：{', '.join(available)}"

        result = f"## {matched['label']}\n\n{matched['content']}"
        return result

    @staticmethod
    def _parse_table_blocks(md: str) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        blocks = re.split(r"\n(?=##|\|)", md)
        for i, block in enumerate(blocks):
            block = block.strip()
            if not block or "|" not in block:
                continue
            label = f"表{i + 1}"
            m = re.match(r"^##\s*(.*)", block)
            if m:
                label = m.group(1).strip()
                block = block[m.end():].strip()
            if block.startswith("|"):
                tables.append({"label": label, "content": block[:3000]})
        return tables

    @staticmethod
    def _extract_tables_from_text(text: str) -> list[dict[str, Any]]:
        """Extract Markdown-style table blocks from merged text."""
        tables: list[dict[str, Any]] = []
        # Find table-like patterns: lines starting with | that have multiple columns
        for m in re.finditer(
            r"(?:^|\n)((?:Table\s*\d+[^\n]*|Tab\.\s*\d+[^\n]*))?\s*\n?"
            r"((?:\|[^\n]+\|\n){2,})",
            text, re.MULTILINE,
        ):
            caption = (m.group(1) or "").strip()
            body = m.group(2).strip()
            if body.count("|") >= 3:
                label = caption if caption else f"表{len(tables) + 1}"
                tables.append({"label": label, "content": body[:3000]})
        return tables

    @staticmethod
    def _find_table(tables: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
        ref_lower = ref.strip().lower()
        # Exact number match: "3" → "Table 3", "Tab. 3", "表3"
        if ref_lower.isdigit():
            patterns = [
                rf"\b(?:table|tab\.?|表)\s*{ref_lower}\b",
                rf"^{ref_lower}[\.\)]",
            ]
            for pat in patterns:
                for t in tables:
                    if re.search(pat, t["label"], re.I):
                        return t
                for t in tables:
                    if re.search(pat, t["content"], re.I):
                        return t

        # Keyword match in label or first rows
        for t in tables:
            blob = (t["label"] + " " + t["content"][:500]).lower()
            if ref_lower in blob:
                return t
        return None


class _ToolResult:
    """Simple result wrapper compatible with old ToolResponse interface."""

    def __init__(self, status: str, text: str, elapsed_ms: int = 0, code: str | None = None) -> None:
        self.status = status
        self.text = text
        self.elapsed_ms = elapsed_ms
        self.error_info = {"code": code} if code else None
