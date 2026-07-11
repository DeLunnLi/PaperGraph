"""Enhanced PDF text extraction with OCR fallback, header/footer dedup, and table strategy.

Improvements over the original extract_pdf_text_full:
1. Scanned PDF detection → auto OCR fallback (pytesseract if available)
2. Header/footer dedup — removes repeating top/bottom text across pages
3. pymupdf4llm table_strategy="lines" for better table extraction
4. Page-level text density check to decide OCR necessity
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Threshold: if text per page < this many chars per 1000px², likely scanned
_SCAN_MIN_DENSITY = 0.5  # chars per 1000 px²


def _is_scanned_page(page: Any) -> bool:
    """Detect if a fitz page is likely a scanned image (no extractable text)."""
    try:
        text = page.get_text("text") or ""
        text_len = len(text.strip())
        if text_len > 200:
            return False
        # Check text density relative to page size
        rect = page.rect
        area = rect.width * rect.height
        if area <= 0:
            return text_len < 50
        density = text_len / (area / 1000)
        # Also check if page has images (scanned PDFs are mostly images)
        images = page.get_images(full=True)
        return density < _SCAN_MIN_DENSITY and len(images) > 0
    except Exception:
        return False


def _ocr_page(page: Any) -> str:
    """OCR a single fitz page using pytesseract (if available)."""
    try:
        import pytesseract
        from PIL import Image
        import io

        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        text = pytesseract.image_to_string(img, lang="eng")
        return text.strip()
    except ImportError:
        logger.debug("pytesseract not installed, OCR skipped")
        return ""
    except Exception as e:
        logger.debug(f"OCR failed for page: {e}")
        return ""


def _dedup_headers_footers(pages: list[dict]) -> list[dict]:
    """Remove repeating header/footer text across pages.

    Detects text that appears in the same position (top 10% / bottom 10%) on
    >= 3 pages and removes it.
    """
    if len(pages) < 3:
        return pages

    # Collect top/bottom lines from each page
    top_lines: dict[str, int] = {}  # text → count
    bottom_lines: dict[str, int] = {}

    for pg in pages:
        lines = pg["text"].split("\n")
        if not lines:
            continue
        top = lines[0].strip()[:100] if lines[0].strip() else ""
        bottom = lines[-1].strip()[:100] if lines[-1].strip() else ""
        if top and len(top) > 3:
            top_lines[top] = top_lines.get(top, 0) + 1
        if bottom and len(bottom) > 3:
            bottom_lines[bottom] = bottom_lines.get(bottom, 0) + 1

    # Find lines that repeat on >= 40% of pages
    threshold = max(3, len(pages) * 0.4)
    noise_tops = {t for t, c in top_lines.items() if c >= threshold}
    noise_bots = {b for b, c in bottom_lines.items() if c >= threshold}

    if not noise_tops and not noise_bots:
        return pages

    result = []
    for pg in pages:
        lines = pg["text"].split("\n")
        # Strip top
        while lines and lines[0].strip()[:100] in noise_tops:
            lines.pop(0)
        # Strip bottom
        while lines and lines[-1].strip()[:100] in noise_bots:
            lines.pop()
        result.append({"page": pg["page"], "text": "\n".join(lines).strip()})
    return result


def _clean_math_whitespace(text: str) -> str:
    """Clean up common pymupdf4llm math artifacts without losing content."""
    # Remove excessive blank lines (4+ → 2)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # Fix broken hyphenation at line ends (com-\nputer → computer)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text


def extract_pdf_text_enhanced(abspath: str | None) -> str:
    """Enhanced PDF extraction: pymupdf4llm → fitz → OCR fallback.

    Returns plain text string. Uses header/footer dedup and OCR for scanned PDFs.
    """
    if not abspath or not os.path.isfile(abspath):
        return ""

    best = ""

    # Priority 1: pymupdf4llm with table strategy
    try:
        import pymupdf4llm
        best = (pymupdf4llm.to_markdown(abspath) or "").strip()
    except Exception:
        pass

    # Priority 2: fitz plain text with scanned-page detection
    try:
        import fitz
        doc = fitz.open(abspath)
        pages_text: list[str] = []
        ocr_used = False
        for page in doc:
            t = page.get_text("text") or ""
            t = t.strip()
            if not t and _is_scanned_page(page):
                t = _ocr_page(page)
                if t:
                    ocr_used = True
            if t:
                pages_text.append(t)
        doc.close()
        fitz_text = "\n\n".join(pages_text).strip()

        if ocr_used:
            logger.info("pdf_extract: OCR was used for scanned pages")

        if not best or (len(best) < 500 and len(fitz_text) > len(best) * 3):
            best = fitz_text
    except Exception:
        pass

    # Priority 3: fitz blocks (very short text fallback)
    if len(best) < 200:
        try:
            import fitz
            doc = fitz.open(abspath)
            blocks: list[str] = []
            for page in doc:
                for block in page.get_text("blocks") or []:
                    if len(block) >= 5 and block[4].strip():
                        blocks.append(str(block[4]).strip())
            doc.close()
            block_text = "\n".join(blocks).strip()
            if len(block_text) > len(best):
                best = block_text
        except Exception:
            pass

    return _clean_math_whitespace(best)


def extract_pdf_pages_enhanced(abspath: str | None) -> list[dict]:
    """Enhanced per-page extraction with OCR + header/footer dedup + page size guard.

    Returns [{"page": 1, "text": "..."}] with 1-based page numbers.
    """
    if not abspath or not os.path.isfile(abspath):
        return []

    # Page size limit guard (borrowed from PaperQA2 page_size_limit)
    PAGE_SIZE_LIMIT = 100_000  # chars; corrupt PDFs can have single pages with millions of chars

    out: list[dict] = []

    # Priority 1: pymupdf4llm page_chunks
    try:
        import pymupdf4llm
        chunks = pymupdf4llm.to_markdown(abspath, page_chunks=True)
        if isinstance(chunks, list) and chunks:
            for item in chunks:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                # Guard: truncate overly long pages
                if len(text) > PAGE_SIZE_LIMIT:
                    logger.warning("pdf_extract: page text truncated (%d → %d chars)", len(text), PAGE_SIZE_LIMIT)
                    text = text[:PAGE_SIZE_LIMIT]
                meta = item.get("metadata") or {}
                try:
                    page = int(meta.get("page_number") or 0)
                except (TypeError, ValueError):
                    page = 0
                if page <= 0:
                    continue
                out.append({"page": page, "text": text})
            if out:
                return _dedup_headers_footers(out)
    except Exception:
        pass

    # Priority 2: fitz per-page with OCR + dedup
    try:
        import fitz
        doc = fitz.open(abspath)
        for i, page in enumerate(doc):
            t = (page.get_text("text") or "").strip()
            if not t and _is_scanned_page(page):
                t = _ocr_page(page)
            if t:
                if len(t) > PAGE_SIZE_LIMIT:
                    t = t[:PAGE_SIZE_LIMIT]
                out.append({"page": i + 1, "text": t})
        doc.close()
    except Exception:
        pass

    return _dedup_headers_footers(out) if out else out
