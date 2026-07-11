"""Sliding-window chunker for RAG-ready text segmentation.

Borrowed from PaperQA2's chunk_pdf strategy: fixed-length chunks with overlap,
each annotated with page range metadata for citation traceability.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Defaults borrowed from PaperQA2 settings.py
DEFAULT_CHUNK_CHARS = 5000
DEFAULT_OVERLAP = 250
DEFAULT_PAGE_SIZE_LIMIT = 100_000  # chars per page; guard against corrupt PDFs


def chunk_pages(
    pages: list[dict[str, Any]],
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    page_size_limit: int = DEFAULT_PAGE_SIZE_LIMIT,
) -> list[dict[str, Any]]:
    """Chunk per-page text into RAG-ready segments with page-range metadata.

    Args:
        pages: [{"page": 1, "text": "..."}] — 1-based page numbers.
        chunk_chars: max chars per chunk.
        overlap: overlap chars between consecutive chunks.
        page_size_limit: max chars per page (guard against corrupt PDFs).

    Returns:
        [{"text": "...", "pages": [1, 2], "page_start": 1, "page_end": 2}]
    """
    if not pages:
        return []

    # Build a flat text with page boundary markers
    # Format: "\x00P{page_num}\x00{text}" — \x00 is unlikely in PDF text
    PAGE_MARK = "\x00P"
    full_text = ""
    page_boundaries: list[tuple[int, int, int]] = []  # (text_start, text_end, page_num)

    for pg in pages:
        page_num = pg.get("page", 0)
        text = str(pg.get("text") or "").strip()
        if not text:
            continue
        # Guard against corrupt pages
        if len(text) > page_size_limit:
            logger.warning(
                "chunk_pages: page %d has %d chars (limit %d), truncating",
                page_num, len(text), page_size_limit,
            )
            text = text[:page_size_limit]

        marker = f"{PAGE_MARK}{page_num}\x00"
        start = len(full_text)
        full_text += marker + text
        end = len(full_text)
        page_boundaries.append((start, end, page_num))

    if not page_boundaries:
        return []

    # Sliding window chunk
    chunks: list[dict[str, Any]] = []
    pos = 0
    text_len = len(full_text)

    while pos < text_len:
        chunk_end = min(pos + chunk_chars, text_len)
        chunk_text = full_text[pos:chunk_end]

        # Find which pages this chunk spans
        chunk_pages_list: list[int] = []
        for (ps, pe, pnum) in page_boundaries:
            # Page overlaps with chunk if page_start < chunk_end and page_end > pos
            if ps < chunk_end and pe > pos:
                chunk_pages_list.append(pnum)

        # Clean chunk text: remove page markers for storage, keep for metadata
        clean_text = chunk_text
        clean_text = clean_text.replace(PAGE_MARK, "")  # remove markers
        # Remove leading page number if present (from marker)
        import re
        clean_text = re.sub(r"^\d+\x00", "", clean_text)
        clean_text = clean_text.replace("\x00", "")
        clean_text = clean_text.strip()

        if clean_text and len(clean_text) >= 50:  # skip tiny chunks
            chunks.append({
                "text": clean_text,
                "pages": chunk_pages_list,
                "page_start": chunk_pages_list[0] if chunk_pages_list else None,
                "page_end": chunk_pages_list[-1] if chunk_pages_list else None,
                "char_count": len(clean_text),
            })

        if chunk_end >= text_len:
            break
        pos = chunk_end - overlap
        if pos <= 0:
            pos = chunk_end  # avoid infinite loop

    logger.info("chunk_pages: %d pages → %d chunks (chunk_chars=%d, overlap=%d)",
                len(pages), len(chunks), chunk_chars, overlap)
    return chunks


def chunk_plain_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[dict[str, Any]]:
    """Chunk plain text (no page info) into segments."""
    if not text or not text.strip():
        return []

    chunks: list[dict[str, Any]] = []
    pos = 0
    text_len = len(text)

    while pos < text_len:
        chunk_end = min(pos + chunk_chars, text_len)
        chunk_text = text[pos:chunk_end].strip()

        if chunk_text and len(chunk_text) >= 50:
            chunks.append({
                "text": chunk_text,
                "char_count": len(chunk_text),
            })

        if chunk_end >= text_len:
            break
        pos = chunk_end - overlap
        if pos <= 0:
            pos = chunk_end

    return chunks
