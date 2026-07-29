"""Secure local-PDF import and metadata enrichment for the paper library."""
from __future__ import annotations

import asyncio
import html
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import fitz
import httpx

from ...core.author import Author
from ...core.paper import Paper
from ...core.paper_paths import library_pdf_relative_path, normalize_library_category_display
from ...settings import get_settings

MAX_LOCAL_PDF_BYTES = 200 * 1024 * 1024
MAX_LOCAL_PDF_PAGES = 5000
MAX_LOCAL_PAGE_EDGE = 20_000
_CHUNK_SIZE = 1024 * 1024
_DOI_RE = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/|\bdoi\s*[:：]?\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)")
_ARXIV_RE = re.compile(r"(?i)(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)")


@dataclass
class ExtractedPdfMetadata:
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    page_count: int = 0


@dataclass
class LocalPdfImportResult:
    paper: Paper
    added: bool
    pdf_attached: bool
    metadata_source: str
    extracted: ExtractedPdfMetadata


def _normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def _normalize_title(value: str | None) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", _normalize_space(value).casefold())


def _clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = _DOI_RE.search(value)
    if not match:
        return None
    return match.group(1).rstrip(".,;:)]}").lower()


def _clean_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    match = _ARXIV_RE.search(value)
    if not match:
        return None
    from app.core.search.normalize import strip_arxiv_version

    return strip_arxiv_version(match.group(1)) or None


def _strip_markup(value: str | None) -> str:
    text = re.sub(r"<[^>]{1,200}>", " ", value or "")
    text = _normalize_space(html.unescape(text))
    return re.sub(r"\s+([.,;:!?，。；：！？])", r"\1", text)


def _split_authors(value: str | None) -> list[str]:
    text = _normalize_space(value)
    if not text:
        return []
    chunks = re.split(r"\s*(?:;|\band\b|、|，)\s*", text, flags=re.I)
    if len(chunks) == 1 and text.count(",") >= 2:
        chunks = [part.strip() for part in text.split(",")]
    output: list[str] = []
    for chunk in chunks:
        name = re.sub(r"\s*\d+(?:,\d+)*\s*$", "", chunk).strip(" ,;*")
        if 2 <= len(name) <= 120 and "@" not in name and name.casefold() not in {"unknown", "anonymous"}:
            output.append(name)
    return output[:80]


def _page_lines(page: fitz.Page) -> list[tuple[float, float, str]]:
    lines: list[tuple[float, float, str]] = []
    for block in (page.get_text("dict") or {}).get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = _normalize_space("".join(str(span.get("text") or "") for span in spans))
            if not text:
                continue
            top = min((float((span.get("bbox") or [0, 0, 0, 0])[1]) for span in spans), default=0)
            size = max((float(span.get("size") or 0) for span in spans), default=0)
            lines.append((top, size, text))
    return sorted(lines, key=lambda item: item[0])


def _guess_title_from_page(page: fitz.Page, fallback: str) -> str:
    candidates: list[tuple[float, float, str]] = []
    page_dict = page.get_text("dict") or {}
    page_height = float(page.rect.height or 1)
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = _normalize_space("".join(str(span.get("text") or "") for span in spans))
            if not text or len(text) < 8 or len(text) > 350:
                continue
            top = min((float((span.get("bbox") or [0, 0, 0, 0])[1]) for span in spans), default=page_height)
            size = max((float(span.get("size") or 0) for span in spans), default=0)
            lower = text.casefold()
            if top > page_height * 0.48 or lower.startswith(("abstract", "arxiv:", "doi:")):
                continue
            if re.fullmatch(r"[\d\W_]+", text):
                continue
            candidates.append((size, -top, text))
    if candidates:
        candidates.sort(reverse=True)
        max_size = candidates[0][0]
        title_lines = [item for item in candidates if item[0] >= max_size * 0.88]
        title_lines.sort(key=lambda item: -item[1])
        title = _normalize_space(" ".join(item[2] for item in title_lines[:4]))
        if 8 <= len(title) <= 500:
            return title
    return fallback


def _guess_authors_from_page(page: fitz.Page, title: str) -> list[str]:
    lines = _page_lines(page)
    title_norm = _normalize_title(title)
    title_bottom = 0.0
    for top, _size, text in lines:
        if title_norm and (_normalize_title(text) in title_norm or title_norm in _normalize_title(text)):
            title_bottom = max(title_bottom, top)
    candidates: list[str] = []
    for top, size, text in lines:
        lower = text.casefold()
        if top <= title_bottom or top > float(page.rect.height) * 0.48:
            continue
        if lower.startswith(("abstract", "摘要", "keywords", "index terms", "doi", "arxiv")):
            break
        if "@" in text or re.search(r"(?i)\b(university|institute|department|laboratory|school|college|academy)\b", text):
            continue
        if 7 <= size <= 16 and 3 <= len(text) <= 300:
            candidates.append(text)
        if len(candidates) >= 3:
            break
    return _split_authors("; ".join(candidates))


def _extract_primary_identifiers(first_text: str) -> tuple[str | None, str | None]:
    # Only trust explicitly labelled identifiers near the beginning of page one.
    front = first_text[:16_000]
    boundary = re.search(r"(?im)^\s*(?:references|bibliography|参考文献)\s*$", front)
    if boundary:
        front = front[:boundary.start()]
    doi = None
    for pattern in (
        r"(?im)^\s*(?:doi|digital object identifier)\s*[:：]\s*(10\.\d{4,9}/\S+)",
        r"(?i)https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s<>]+)",
    ):
        match = re.search(pattern, front)
        if match:
            doi = _clean_doi(match.group(1))
            break
    arxiv_id = _clean_arxiv_id(front)
    return doi, arxiv_id


def extract_pdf_metadata(path: str, original_filename: str = "") -> ExtractedPdfMetadata:
    fallback = _normalize_space(Path(original_filename or path).stem.replace("_", " ").replace("-", " ")) or "未命名论文"
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ValueError("PDF 文件损坏或无法解析") from exc
    try:
        if document.needs_pass:
            raise ValueError("暂不支持加密 PDF")
        if document.page_count < 1:
            raise ValueError("PDF 没有可读取页面")
        if document.page_count > MAX_LOCAL_PDF_PAGES:
            raise ValueError(f"PDF 页数超过 {MAX_LOCAL_PDF_PAGES} 页限制")
        metadata = document.metadata or {}
        page = document.load_page(0)
        if max(float(page.rect.width), float(page.rect.height)) > MAX_LOCAL_PAGE_EDGE:
            raise ValueError("PDF 页面尺寸异常，无法安全解析")
        first_text = (page.get_text("text") or "")[:80_000]
        extra_text = ""
        for index in range(1, min(document.page_count, 3)):
            extra_text += "\n" + (document.load_page(index).get_text("text") or "")
        meta_title = _normalize_space(metadata.get("title"))
        noisy_meta_title = bool(re.search(r"(?i)(microsoft word|\.docx?$|^untitled$|^unknown$|[/\\])", meta_title))
        visual_title = _guess_title_from_page(page, fallback)
        title = meta_title if len(meta_title) >= 8 and not noisy_meta_title else visual_title
        authors = _split_authors(metadata.get("author")) or _guess_authors_from_page(page, visual_title)
        doi, arxiv_id = _extract_primary_identifiers(first_text)
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", first_text[:12_000])
        abstract = None
        abstract_match = re.search(
            r"(?is)\babstract\b\s*[:—-]?\s*(.{80,6000}?)(?=\n\s*(?:1\.?\s+)?(?:introduction|keywords?|index terms)\b)",
            first_text,
        )
        if abstract_match:
            abstract = _strip_markup(abstract_match.group(1))[:5000]
        return ExtractedPdfMetadata(
            title=title[:500], authors=authors, abstract=abstract, doi=doi,
            arxiv_id=arxiv_id, year=int(year_match.group(1)) if year_match else None,
            page_count=document.page_count,
        )
    finally:
        document.close()


def save_upload_to_temp(stream: BinaryIO, original_filename: str, data_dir: str) -> str:
    temp_root = os.path.realpath(os.path.join(data_dir, ".imports"))
    data_root = os.path.realpath(data_dir)
    if not temp_root.startswith(data_root + os.sep):
        raise ValueError("临时上传目录非法")
    os.makedirs(temp_root, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="pdf-", suffix=".upload", dir=temp_root)
    total = 0
    try:
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_LOCAL_PDF_BYTES:
                    raise ValueError("PDF 文件不能超过 200 MiB")
                target.write(chunk)
        if total < 5:
            raise ValueError("PDF 文件为空")
        with open(path, "rb") as probe:
            if probe.read(5) != b"%PDF-":
                raise ValueError("上传内容不是有效的 PDF 文件")
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def _crossref_message_to_paper(message: dict) -> Paper | None:
    titles = message.get("title") or []
    title = _normalize_space(titles[0] if titles else "")
    if not title:
        return None
    authors = []
    for entry in (message.get("author") or [])[:80]:
        name = _normalize_space(" ".join(filter(None, [entry.get("given"), entry.get("family")])))
        if name:
            authors.append(Author(name=name, orcid=_normalize_space(entry.get("ORCID")) or None))
    date_parts = ((message.get("published-print") or message.get("published-online") or message.get("issued") or {}).get("date-parts") or [])
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    containers = message.get("container-title") or []
    doi = _clean_doi(message.get("DOI"))
    return Paper(
        title=title, authors=authors, abstract=_strip_markup(message.get("abstract")) or None,
        doi=doi, journal=_normalize_space(containers[0] if containers else "") or None,
        year=int(year) if year else None, publisher=_normalize_space(message.get("publisher")) or None,
        source_url=f"https://doi.org/{doi}" if doi else _normalize_space(message.get("URL")) or None,
        source="crossref",
    )


async def _lookup_crossref(doi: str) -> Paper | None:
    settings = get_settings()
    headers = {"User-Agent": f"PaperGraph/0.1 ({settings.openalex_mailto or settings.ncbi_email or 'local-import'})"}
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=False, trust_env=settings.papergraph_httpx_trust_env) as client:
            response = await client.get(f"https://api.crossref.org/works/{quote(doi, safe='')}", headers=headers)
            response.raise_for_status()
            return _crossref_message_to_paper((response.json() or {}).get("message") or {})
    except Exception:
        return None


def _title_match(left_raw: str | None, right_raw: str | None) -> bool:
    left, right = _normalize_title(left_raw), _normalize_title(right_raw)
    if not left or not right or min(len(left), len(right)) < 12:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 18 and shorter in longer and len(shorter) / len(longer) >= 0.92


def _author_name_key(value: str) -> str:
    parts = re.findall(r"[\w\u4e00-\u9fff]+", _normalize_space(value).casefold())
    return "".join(parts[-2:]) if parts else ""


def _metadata_match(candidate: Paper, extracted: ExtractedPdfMetadata) -> bool:
    candidate_doi = _clean_doi(candidate.doi)
    candidate_arxiv = _clean_arxiv_id(f"arXiv:{candidate.arxiv_id or ''}")
    if extracted.doi and candidate_doi and candidate_doi != extracted.doi:
        return False
    if extracted.arxiv_id and candidate_arxiv and candidate_arxiv != extracted.arxiv_id:
        return False
    if not _title_match(candidate.title, extracted.title):
        return False
    if extracted.year and candidate.year and abs(int(extracted.year) - int(candidate.year)) > 1:
        return False
    if extracted.authors and candidate.authors:
        local_keys = {_author_name_key(name) for name in extracted.authors}
        remote_keys = {_author_name_key(author.name) for author in candidate.authors}
        local_keys.discard("")
        remote_keys.discard("")
        if local_keys and remote_keys and not (local_keys & remote_keys):
            return False
    return True


async def enrich_pdf_metadata(extracted: ExtractedPdfMetadata, searcher) -> tuple[Paper | None, str]:
    if extracted.doi:
        crossref = await _lookup_crossref(extracted.doi)
        if crossref and _metadata_match(crossref, extracted):
            return crossref, "crossref"
    if extracted.arxiv_id:
        try:
            results = await searcher.search_arxiv_async(
                "", max_results=2, arxiv_id_list=[extracted.arxiv_id], http_timeout_sec=15, http_max_attempts=1,
            )
            if results and _metadata_match(results[0], extracted):
                return results[0], "arxiv"
        except Exception:
            pass
    if len(_normalize_title(extracted.title)) >= 12:
        try:
            results = await asyncio.wait_for(
                searcher.search_async(
                    extracted.title, sources=["openalex", "dblp", "arxiv"], max_results=5,
                    http_timeout_sec=15, http_max_attempts=1, sort="relevance",
                ),
                timeout=24,
            )
            for candidate in results:
                if _metadata_match(candidate, extracted):
                    return candidate, str(candidate.source or "external")
        except Exception:
            pass
    return None, "pdf"


def _merge_metadata(extracted: ExtractedPdfMetadata, enriched: Paper | None, category: str | None, user_id: int) -> Paper:
    base = enriched or Paper(title=extracted.title)
    base.title = _normalize_space(base.title) or extracted.title
    if not base.authors and extracted.authors:
        base.authors = [Author(name=name) for name in extracted.authors]
    base.abstract = base.abstract or extracted.abstract
    enriched_doi = _clean_doi(base.doi)
    enriched_arxiv = _clean_arxiv_id(f"arXiv:{base.arxiv_id or ''}")
    if enriched_doi and extracted.doi and enriched_doi != extracted.doi:
        raise ValueError("PDF 中的 DOI 与外部元数据冲突，已停止自动合并")
    if enriched_arxiv and extracted.arxiv_id and enriched_arxiv != extracted.arxiv_id:
        raise ValueError("PDF 中的 arXiv ID 与外部元数据冲突，已停止自动合并")
    base.doi = enriched_doi or extracted.doi
    base.arxiv_id = enriched_arxiv or extracted.arxiv_id
    base.year = base.year or extracted.year
    base.category = normalize_library_category_display(category or base.category)
    base.user_id = int(user_id)
    base.tags = list(dict.fromkeys([*(base.tags or []), "本地导入"]))[:24]
    if not enriched:
        base.source = "local"
    return base


def _find_exact_title_paper(db, title: str, user_id: int) -> Paper | None:
    wanted = _normalize_title(title)
    if not wanted:
        return None
    for candidate in db.search_library(query=title, limit=20, user_id=user_id):
        if _normalize_title(candidate.title) == wanted:
            return candidate
    return None


async def import_local_pdf(
    *, temp_path: str, original_filename: str, category: str | None,
    auto_enrich: bool, auto_classify: bool, user_id: int, db, searcher,
) -> LocalPdfImportResult:
    extracted = await asyncio.to_thread(extract_pdf_metadata, temp_path, original_filename)
    enriched, metadata_source = (await enrich_pdf_metadata(extracted, searcher)) if auto_enrich else (None, "pdf")
    paper = _merge_metadata(extracted, enriched, category, user_id)
    if auto_classify and not category:
        try:
            from ...agents.paper_analysis_agent import PaperAnalysisAgent
            existing = [item["category"] for item in db.list_library_category_folders(user_id=user_id)]
            assigned, extra_tags = await asyncio.to_thread(
                PaperAnalysisAgent().classify_for_library,
                paper.title, paper.abstract, paper.journal, paper.keywords, existing,
            )
            paper.category = normalize_library_category_display(assigned)
            paper.tags = list(dict.fromkeys([*(paper.tags or []), *(extra_tags or [])]))[:24]
        except Exception:
            paper.category = normalize_library_category_display(category)

    existing_by_title = None
    if not any((paper.doi, paper.arxiv_id, paper.pmid, paper.pmc_id)):
        existing_by_title = _find_exact_title_paper(db, paper.title, user_id)
    if existing_by_title and existing_by_title.id is not None:
        paper_id, added = int(existing_by_title.id), False
    else:
        paper_id, added = db.add_paper(paper)
    stored = db.get_paper_by_id(paper_id, user_id=user_id)
    if not stored:
        raise RuntimeError("文献写入后无法读取")

    pdf_attached = False
    existing_path = (stored.local_pdf_path or "").strip()
    if not existing_path:
        relative_path = library_pdf_relative_path(stored.category or paper.category, paper_id, stored.title or paper.title)
        data_root = os.path.realpath(get_settings().data_dir)
        destination = os.path.realpath(os.path.join(data_root, relative_path))
        if not destination.startswith(data_root + os.sep):
            if added:
                db.delete_paper(paper_id, user_id=user_id)
            raise ValueError("PDF 目标路径非法")
        try:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            os.replace(temp_path, destination)
            if not db.set_local_pdf_path(paper_id, relative_path, user_id=user_id):
                raise RuntimeError("无法关联本地 PDF")
            pdf_attached = True
        except Exception:
            try:
                os.remove(destination)
            except OSError:
                pass
            if added:
                db.delete_paper(paper_id, user_id=user_id)
            raise
    else:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    final_paper = db.get_paper_by_id(paper_id, user_id=user_id) or stored
    return LocalPdfImportResult(
        paper=final_paper, added=added, pdf_attached=pdf_attached,
        metadata_source=metadata_source, extracted=extracted,
    )


def cleanup_temp_file(path: str | None) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
