from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from app.core.storage import PaperDatabase
from app.core.author import Author
from app.core.paper import Paper
from app.services.papers.local_pdf_import import (
    ExtractedPdfMetadata,
    _metadata_match,
    _strip_markup,
    extract_pdf_metadata,
    import_local_pdf,
    save_upload_to_temp,
)


def _make_pdf(path: Path, *, title: str = "Reliable Local Import for Research Papers") -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontsize=20)
    page.insert_text((72, 110), "Alice Researcher and Bob Scientist", fontsize=11)
    page.insert_text((72, 140), "arXiv: 2607.01234", fontsize=10)
    page.insert_text((72, 160), "DOI: 10.1234/local.import.2026", fontsize=10)
    page.insert_text(
        (72, 200),
        "Abstract This abstract contains enough text to validate local PDF metadata extraction reliably.\n1 Introduction",
        fontsize=10,
    )
    doc.set_metadata({"author": "Alice Researcher; Bob Scientist"})
    doc.save(str(path))
    doc.close()


def test_extract_pdf_metadata(tmp_path):
    path = tmp_path / "paper.pdf"
    _make_pdf(path)

    metadata = extract_pdf_metadata(str(path), path.name)

    assert metadata.title == "Reliable Local Import for Research Papers"
    assert metadata.authors == ["Alice Researcher", "Bob Scientist"]
    assert metadata.doi == "10.1234/local.import.2026"
    assert metadata.arxiv_id == "2607.01234"
    assert metadata.page_count == 1


def test_extract_ignores_unlabelled_reference_doi(tmp_path):
    path = tmp_path / "reference-only.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A Paper Without Its Own Digital Identifier", fontsize=20)
    page.insert_text((72, 110), "Alice Researcher and Bob Scientist", fontsize=11)
    page.insert_text((72, 160), "Abstract This paper has no DOI of its own but discusses prior work.\n1 Introduction", fontsize=10)
    page.insert_text((72, 230), "Prior work is available as 10.9999/not.this.paper", fontsize=10)
    doc.save(str(path))
    doc.close()

    metadata = extract_pdf_metadata(str(path), path.name)

    assert metadata.doi is None
    assert metadata.authors == ["Alice Researcher", "Bob Scientist"]


def test_metadata_match_rejects_conflicting_identifiers_and_authors():
    extracted = ExtractedPdfMetadata(
        title="Reliable Local Import for Research Papers",
        authors=["Alice Researcher"],
        doi="10.1234/local.import.2026",
        year=2026,
    )
    assert not _metadata_match(
        Paper(title=extracted.title, doi="10.9999/different", authors=[Author(name="Alice Researcher")], year=2026),
        extracted,
    )
    assert not _metadata_match(
        Paper(title=extracted.title, doi=extracted.doi, authors=[Author(name="Someone Else")], year=2026),
        extracted,
    )
    assert _metadata_match(
        Paper(title=extracted.title, doi=extracted.doi, authors=[Author(name="Alice Researcher")], year=2026),
        extracted,
    )


def test_crossref_markup_is_cleaned():
    assert _strip_markup("<jats:p>Useful &amp; verified <b>abstract</b>.</jats:p>") == "Useful & verified abstract."


def test_upload_rejects_non_pdf_magic(tmp_path):
    with pytest.raises(ValueError, match="不是有效的 PDF"):
        save_upload_to_temp(io.BytesIO(b"not-a-pdf"), "fake.pdf", str(tmp_path))
    imports = tmp_path / ".imports"
    assert not imports.exists() or not list(imports.iterdir())


def test_upload_rejects_oversized_stream(tmp_path, monkeypatch):
    import app.services.papers.local_pdf_import as module

    monkeypatch.setattr(module, "MAX_LOCAL_PDF_BYTES", 8)
    with pytest.raises(ValueError, match="不能超过"):
        save_upload_to_temp(io.BytesIO(b"%PDF-123456789"), "large.pdf", str(tmp_path))


def test_import_local_pdf_saves_and_deduplicates(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = PaperDatabase(str(data_dir / "papers.db"))
    source = tmp_path / "fixture.pdf"
    _make_pdf(source)

    import app.services.papers.local_pdf_import as module
    monkeypatch.setattr(module.get_settings(), "data_dir", str(data_dir))

    class NoNetworkSearcher:
        async def search_async(self, *args, **kwargs):
            return []

        async def search_arxiv_async(self, *args, **kwargs):
            return []

    with source.open("rb") as source_stream:
        first_temp = save_upload_to_temp(source_stream, source.name, str(data_dir))
    first = asyncio.run(import_local_pdf(
        temp_path=first_temp,
        original_filename=source.name,
        category="测试/本地导入",
        auto_enrich=False,
        auto_classify=False,
        user_id=7,
        db=db,
        searcher=NoNetworkSearcher(),
    ))
    assert first.added is True
    assert first.pdf_attached is True
    assert first.paper.local_pdf_path
    stored_path = data_dir / first.paper.local_pdf_path
    assert stored_path.is_file()
    assert stored_path.read_bytes()[:5] == b"%PDF-"

    with source.open("rb") as source_stream:
        second_temp = save_upload_to_temp(source_stream, source.name, str(data_dir))
    second = asyncio.run(import_local_pdf(
        temp_path=second_temp,
        original_filename=source.name,
        category="测试/本地导入",
        auto_enrich=False,
        auto_classify=False,
        user_id=7,
        db=db,
        searcher=NoNetworkSearcher(),
    ))
    assert second.added is False
    assert second.paper.id == first.paper.id
    assert db.count_papers(user_id=7) == 1
    assert not os.path.exists(second_temp)
