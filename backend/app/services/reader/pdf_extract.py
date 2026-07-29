"""Enhanced PDF text extraction with OCR fallback, header/footer dedup, and table strategy.

Improvements over the original extract_pdf_text_full:
1. Scanned PDF detection → auto OCR fallback (pytesseract if available)
2. Header/footer dedup — removes repeating top/bottom text across pages
3. pymupdf4llm table_strategy="lines" for better table extraction
4. Page-level text density check to decide OCR necessity
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Threshold: if text per page < this many chars per 1000px², likely scanned
_SCAN_MIN_DENSITY = 0.5  # chars per 1000 px²
_OCR_DEFAULT_ENDPOINT = "https://aigc.sankuai.com/v1/openai/native/chat/completions"
_OCR_CACHE_MAX_ITEMS = 128
_ocr_cache: OrderedDict[str, str] = OrderedDict()
_ocr_cache_lock = threading.Lock()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


_ocr_remote_slots = threading.BoundedSemaphore(max(1, _env_int("PAPERGRAPH_OCR_CONCURRENCY", 2, 1, 16)))


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
    except Exception as exc:
        logger.debug("pdf_extract: _is_scanned_page failed: %s", exc, exc_info=False)
        return False


def _cache_ocr_get(fingerprint: str) -> str | None:
    with _ocr_cache_lock:
        value = _ocr_cache.get(fingerprint)
        if value is not None:
            _ocr_cache.move_to_end(fingerprint)
        return value


def _cache_ocr_set(fingerprint: str, text: str) -> None:
    with _ocr_cache_lock:
        _ocr_cache[fingerprint] = text
        _ocr_cache.move_to_end(fingerprint)
        while len(_ocr_cache) > _OCR_CACHE_MAX_ITEMS:
            _ocr_cache.popitem(last=False)


def _render_ocr_image(page: Any) -> tuple[bytes, str]:
    """Render a bounded page image suitable for Friday's 5 MB image limit."""
    dpi = _env_int("PAPERGRAPH_OCR_DPI", 180, 96, 240)
    max_bytes = _env_int("PAPERGRAPH_OCR_MAX_IMAGE_BYTES", 4_500_000, 100_000, 5_000_000)
    for quality in (85, 70, 55):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        image = pix.tobytes("jpeg", jpg_quality=quality)
        if len(image) <= max_bytes:
            return image, "image/jpeg"
        dpi = max(96, int(dpi * 0.8))
    return b"", ""


def _friday_ocr(image: bytes, mime_type: str) -> str:
    provider = os.getenv("PAPERGRAPH_OCR_PROVIDER", "").strip().lower()
    if provider not in {"friday", "longcat"}:
        return ""
    api_key = (os.getenv("PAPERGRAPH_OCR_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key or not image or not mime_type:
        return ""

    import httpx

    endpoint = os.getenv("PAPERGRAPH_OCR_ENDPOINT", _OCR_DEFAULT_ENDPOINT).strip()
    model = os.getenv("PAPERGRAPH_OCR_MODEL", "LongCat-VL-8B").strip()
    timeout = _env_int("PAPERGRAPH_OCR_TIMEOUT_SEC", 60, 5, 180)
    encoded = base64.b64encode(image).decode("ascii")
    payload = {
        "stream": False,
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Perform OCR on this academic document page. Transcribe every visible "
                        "character exactly in reading order. Preserve paragraphs, headings, formulas, "
                        "tables, and reference entries as plain text. Return only the transcription, "
                        "without commentary or Markdown fences."
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ],
        }],
        "max_new_tokens": _env_int("PAPERGRAPH_OCR_MAX_TOKENS", 4096, 256, 8192),
        "temperature": 0,
    }
    trust_env = os.getenv("LLM_DISABLE_PROXY", "0").strip().lower() not in {"1", "true", "yes", "on"}
    with _ocr_remote_slots, httpx.Client(timeout=timeout, trust_env=trust_env) as client:
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices else None
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict)
        )
    text = str(content or "").strip()
    fence_match = re.fullmatch(r"```(?:text|markdown)?\s*\n?(.*?)\n?```", text, flags=re.DOTALL | re.IGNORECASE)
    return (fence_match.group(1) if fence_match else text).strip()


def _local_ocr(image: bytes) -> str:
    """Run pytesseract when installed; this is also the remote OCR fallback."""
    try:
        import io

        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(io.BytesIO(image)), lang="eng").strip()
    except ImportError:
        logger.debug("pytesseract not installed, local OCR skipped")
        return ""
    except Exception as exc:
        logger.debug("Local OCR failed for page: %s", exc, exc_info=False)
        return ""


def _ocr_page(page: Any) -> str:
    """OCR one page through configured Friday Vision, then fall back locally."""
    try:
        image, mime_type = _render_ocr_image(page)
        if not image:
            return ""
        fingerprint = hashlib.sha256(image).hexdigest()
        cached = _cache_ocr_get(fingerprint)
        if cached is not None:
            return cached
        text = ""
        try:
            text = _friday_ocr(image, mime_type)
        except Exception as exc:
            logger.warning("Friday OCR failed; falling back locally: %s", exc, exc_info=False)
        if not text:
            text = _local_ocr(image)
        _cache_ocr_set(fingerprint, text)
        return text
    except Exception as exc:
        logger.debug("OCR failed for page: %s", exc, exc_info=False)
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
    except Exception as exc:
        logger.debug("pdf_extract: pymupdf4llm strategy failed: %s", exc, exc_info=False)

    # Priority 2: fitz plain text with scanned-page detection
    # When pymupdf4llm already produced enough text (best >= 500 chars), the
    # fitz result can never replace it (the condition below requires
    # len(best) < 500), so OCR on scanned pages would be pure waste — skip it.
    # OCR still runs when pymupdf4llm came up short, which is the only case
    # fitz_text can win.
    ocr_worthwhile = not best or len(best) < 500
    try:
        import fitz
        doc = fitz.open(abspath)
        pages_text: list[str] = []
        ocr_used = False
        ocr_pages = 0
        max_ocr_pages = _env_int("PAPERGRAPH_OCR_MAX_PAGES", 30, 0, 100)
        for page in doc:
            t = (page.get_text("text") or "").strip()
            if ocr_worthwhile and _is_scanned_page(page) and ocr_pages < max_ocr_pages:
                ocr_pages += 1
                ocr_text = _ocr_page(page)
                if len(ocr_text) > len(t):
                    t = ocr_text
                    ocr_used = True
            if t:
                pages_text.append(t)
        doc.close()
        fitz_text = "\n\n".join(pages_text).strip()

        if ocr_used:
            logger.info("pdf_extract: OCR was used for scanned pages")

        if not best or (len(best) < 500 and len(fitz_text) > len(best) * 3):
            best = fitz_text
    except Exception as exc:
        logger.debug("pdf_extract: fitz text strategy failed: %s", exc, exc_info=False)

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
        except Exception as exc:
            logger.debug("pdf_extract: fitz blocks strategy failed: %s", exc, exc_info=False)

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
    except Exception as exc:
        logger.debug("pdf_extract: pymupdf4llm page_chunks strategy failed: %s", exc, exc_info=False)

    # Priority 2: fitz per-page with OCR + dedup
    try:
        import fitz
        doc = fitz.open(abspath)
        ocr_pages = 0
        max_ocr_pages = _env_int("PAPERGRAPH_OCR_MAX_PAGES", 30, 0, 100)
        for i, page in enumerate(doc):
            t = (page.get_text("text") or "").strip()
            if _is_scanned_page(page) and ocr_pages < max_ocr_pages:
                ocr_pages += 1
                ocr_text = _ocr_page(page)
                if len(ocr_text) > len(t):
                    t = ocr_text
            if t:
                if len(t) > PAGE_SIZE_LIMIT:
                    t = t[:PAGE_SIZE_LIMIT]
                out.append({"page": i + 1, "text": t})
        doc.close()
    except Exception as exc:
        logger.debug("pdf_extract: fitz per-page strategy failed: %s", exc, exc_info=False)

    return _dedup_headers_footers(out) if out else out
