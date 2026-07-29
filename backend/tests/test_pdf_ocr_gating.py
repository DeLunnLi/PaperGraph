"""Tests for extract_pdf_text_enhanced OCR gating — locks in the round-1 fix:
when pymupdf4llm already produced >= 500 chars, OCR on scanned pages is skipped
(the fitz result can never replace `best` in that case, so OCR would be wasted).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

fitz = pytest.importorskip("fitz")

from app.services.reader import pdf_extract


@pytest.fixture
def sample_pdf(tmp_path) -> str:
    """A text PDF (NOT scanned) so _is_scanned_page is False; OCR path is gated
    independently of scanned-ness via the `ocr_worthwhile` flag."""
    p = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # >500 chars so ocr_worthwhile becomes False.
    page.insert_text((72, 72), "WORD " * 120)
    doc.save(str(p))
    doc.close()
    return str(p)


def _install_pymupdf4llm_stub(monkeypatch, text: str) -> None:
    """Inject a fake pymupdf4llm module so Priority 1 yields `text`."""
    import types
    mod = types.ModuleType("pymupdf4llm")
    mod.to_markdown = lambda abspath, **kw: text  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymupdf4llm", mod)


def test_ocr_skipped_when_pymupdf4llm_text_is_long(sample_pdf, monkeypatch):
    _install_pymupdf4llm_stub(monkeypatch, "x" * 600)

    ocr_calls = {"n": 0}
    def _ocr_page(page):
        ocr_calls["n"] += 1
        return ""
    monkeypatch.setattr(pdf_extract, "_ocr_page", _ocr_page)
    # Force _is_scanned_page True so the only thing preventing OCR is the gate.
    monkeypatch.setattr(pdf_extract, "_is_scanned_page", lambda page: True)

    pdf_extract.extract_pdf_text_enhanced(sample_pdf)
    assert ocr_calls["n"] == 0, "OCR must be skipped when best >= 500 chars"


def test_ocr_runs_when_pymupdf4llm_text_is_short(sample_pdf, monkeypatch):
    _install_pymupdf4llm_stub(monkeypatch, "x" * 100)  # < 500 → ocr_worthwhile True

    ocr_calls = {"n": 0}
    def _ocr_page(page):
        ocr_calls["n"] += 1
        return "ocr text " + "y" * 200
    monkeypatch.setattr(pdf_extract, "_ocr_page", _ocr_page)
    monkeypatch.setattr(pdf_extract, "_is_scanned_page", lambda page: True)

    pdf_extract.extract_pdf_text_enhanced(sample_pdf)
    assert ocr_calls["n"] >= 1, "OCR must run when best < 500 chars and pages are scanned"
