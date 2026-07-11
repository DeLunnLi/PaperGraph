"""Tests for extract_pdf_text_with_pages — per-page PDF text extraction.

Generates a small 3-page PDF fixture with fitz (pymupdf) and asserts the
extractor returns a list of {"page": N, "text": str} dicts with 1-based pages.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest

fitz = pytest.importorskip("fitz")

from app.services.reader.paper_reader_context import extract_pdf_text_with_pages


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory) -> str:
    """Build a 3-page PDF where each page contains a distinctive marker line."""
    p = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    doc = fitz.open()
    for i in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"PAGE_{i}_MARKER content line.")
    doc.save(str(p))
    doc.close()
    return str(p)


def test_returns_list_of_page_dicts(sample_pdf):
    pages = extract_pdf_text_with_pages(sample_pdf)
    assert isinstance(pages, list)
    assert len(pages) == 3
    for pg in pages:
        assert isinstance(pg, dict)
        assert "page" in pg and "text" in pg
        assert isinstance(pg["page"], int) and pg["page"] >= 1
        assert isinstance(pg["text"], str) and pg["text"].strip()


def test_pages_are_1_based_and_ordered(sample_pdf):
    pages = extract_pdf_text_with_pages(sample_pdf)
    nums = [pg["page"] for pg in pages]
    assert nums == [1, 2, 3]


def test_page_text_contains_marker(sample_pdf):
    pages = extract_pdf_text_with_pages(sample_pdf)
    for pg in pages:
        assert f"PAGE_{pg['page']}_MARKER" in pg["text"]


def test_missing_file_returns_empty():
    assert extract_pdf_text_with_pages("/nonexistent/path/to/file.pdf") == []


def test_none_path_returns_empty():
    assert extract_pdf_text_with_pages(None) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
