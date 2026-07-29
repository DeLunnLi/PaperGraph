"""Tests for compute_and_cache_excerpt cache short-circuit — locks in the
round-1 perf fix: a fresh cached excerpt for an unchanged PDF must skip the
seconds-level re-parse that _schedule_pdf_excerpt would otherwise run on every
reader chat message.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

fitz = pytest.importorskip("fitz")

from app.services.reader import paper_reader_context as prc
from app.services.reader.paper_reader_context import (
    _cache_set,
    compute_and_cache_excerpt,
)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "excerpt.db")


@pytest.fixture
def sample_pdf(tmp_path) -> str:
    p = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"PAGE_{i}_MARKER content line number {i}.")
    doc.save(str(p))
    doc.close()
    return str(p)


def test_cached_excerpt_skips_reparse(db_path, sample_pdf, monkeypatch):
    # Pre-populate the cache for this exact PDF.
    _cache_set(db_path, 42, sample_pdf, "cached excerpt text", None)

    # If the guard regresses, extract_pdf_text_full is called and we fail loud.
    def _boom(abspath):
        raise AssertionError("extract_pdf_text_full should not run on a cache hit")
    monkeypatch.setattr(prc, "extract_pdf_text_full", _boom)
    monkeypatch.setattr(prc, "extract_pdf_text_with_pages", _boom)

    compute_and_cache_excerpt(db_path, 42, sample_pdf)  # must return without parsing


def test_missing_cache_falls_through_to_parse(db_path, sample_pdf, monkeypatch):
    # No cache entry → extract_pdf_text_full runs; stub it to a known string and
    # stub the per-page extractor to avoid a second real parse.
    calls = {"n": 0}

    def _fake_full(abspath):
        calls["n"] += 1
        return "freshly parsed excerpt"
    monkeypatch.setattr(prc, "extract_pdf_text_full", _fake_full)
    monkeypatch.setattr(prc, "extract_pdf_text_with_pages", lambda abspath: [])

    compute_and_cache_excerpt(db_path, 99, sample_pdf)
    assert calls["n"] == 1
