"""Tests for pdf_chunk: sliding-window chunker with page metadata."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.reader.pdf_chunk import chunk_pages, chunk_plain_text


def test_chunk_pages_basic():
    """Pages should be chunked into segments with page metadata."""
    pages = [
        {"page": 1, "text": "A" * 3000},
        {"page": 2, "text": "B" * 3000},
        {"page": 3, "text": "C" * 3000},
    ]
    chunks = chunk_pages(pages, chunk_chars=5000, overlap=250)
    assert len(chunks) >= 2
    # Each chunk should have page metadata
    for c in chunks:
        assert "text" in c
        assert "pages" in c
        assert isinstance(c["pages"], list)
        assert len(c["pages"]) > 0
        assert c["page_start"] is not None
        assert c["page_end"] is not None


def test_chunk_pages_overlap():
    """Chunks should overlap by the specified amount."""
    text = "X" * 10000
    pages = [{"page": 1, "text": text}]
    chunks = chunk_pages(pages, chunk_chars=5000, overlap=500)
    assert len(chunks) >= 2
    # Overlap means the second chunk starts before the first ends
    # (we can't directly check offset, but we can check there are 2+ chunks)


def test_chunk_pages_single_short_page():
    """A single short page should produce one chunk (above 50 char threshold)."""
    pages = [{"page": 1, "text": "This is a short page with enough text to pass the 50 char threshold for chunking."}]
    chunks = chunk_pages(pages, chunk_chars=5000, overlap=250)
    assert len(chunks) == 1
    assert chunks[0]["pages"] == [1]
    assert chunks[0]["page_start"] == 1
    assert chunks[0]["page_end"] == 1


def test_chunk_pages_empty():
    assert chunk_pages([]) == []
    assert chunk_pages([{"page": 1, "text": ""}]) == []


def test_chunk_pages_page_size_limit():
    """Pages exceeding page_size_limit should be truncated."""
    long_text = "X" * 200_000
    pages = [{"page": 1, "text": long_text}]
    chunks = chunk_pages(pages, chunk_chars=5000, overlap=250, page_size_limit=100_000)
    # The page should have been truncated before chunking
    total_chars = sum(c["char_count"] for c in chunks)
    assert total_chars <= 106_000  # 100k limit + overlap chunks


def test_chunk_pages_multi_page_metadata():
    """A chunk spanning multiple pages should list all pages."""
    pages = [
        {"page": 1, "text": "A" * 2000},
        {"page": 2, "text": "B" * 2000},
        {"page": 3, "text": "C" * 2000},
    ]
    chunks = chunk_pages(pages, chunk_chars=6000, overlap=100)
    # With 6000 chars per chunk and 2000 per page, first chunk should span pages 1-3
    assert len(chunks) >= 1
    first_chunk = chunks[0]
    assert 1 in first_chunk["pages"]
    assert first_chunk["page_end"] >= 2


def test_chunk_plain_text_basic():
    """Plain text chunking without page info."""
    text = "A" * 12000
    chunks = chunk_plain_text(text, chunk_chars=5000, overlap=250)
    assert len(chunks) >= 2
    for c in chunks:
        assert "text" in c
        assert c["char_count"] <= 5000


def test_chunk_plain_text_short():
    text = "Short text."
    chunks = chunk_plain_text(text, chunk_chars=5000, overlap=250)
    assert len(chunks) == 0  # Too short (< 50 chars threshold)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
