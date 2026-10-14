"""Library filtering/count regressions using only temporary SQLite databases."""
import json

import pytest

from app.core.storage import PaperDatabase
from app.models.schemas import Paper as ApiPaper, ReadStatus
from app.services.papers.papers_library_service import get_library


@pytest.fixture
def db(tmp_path):
    return PaperDatabase(str(tmp_path / "papers.db"))


def add_paper(db, paper_id, *, tags=None, raw_tags=None, title="Graph learning", **fields):
    values = {
        "id": paper_id,
        "title": title,
        "user_id": 1,
        "tags": json.dumps(tags or []) if raw_tags is None else raw_tags,
        "created_at": "2026-01-01 00:00:00",
        "local_pdf_path": "pdfs/test.pdf",
        **fields,
    }
    db._query(
        f"INSERT INTO papers ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        list(values.values()),
    )


def test_tag_filter_precedes_limit_and_offset_and_uses_or_semantics(db):
    add_paper(db, 1, tags=["ML"])
    add_paper(db, 2, tags=["Graph"])
    add_paper(db, 3, tags=["Other"])
    add_paper(db, 4, tags=["Other"])
    filters = dict(tags=["ML", "Graph"], user_id=1)
    assert [p.id for p in db.search_library(**filters, limit=1)] == [2]
    assert [p.id for p in db.search_library(**filters, limit=1, offset=1)] == [1]
    assert db.count_search_library(**filters) == 2


def test_tag_filter_is_case_sensitive_and_tolerates_legacy_json(db):
    rows = [
        '["Graph"]', '["graph"]', '[" Graph "]', "not json", '"Graph"',
        "32", '{"Graph": true}', '[{"name": "Graph"}]', "null", "[]",
        '["Other", "Graph", "Graph"]',
    ]
    for paper_id, raw in enumerate(rows, start=1):
        add_paper(db, paper_id, raw_tags=raw)
    filters = dict(tags=["Graph"], user_id=1)
    assert [p.id for p in db.search_library(**filters)] == [11, 1]
    assert db.count_search_library(**filters) == 2


@pytest.mark.parametrize("fts_enabled", [False, True])
def test_page_and_count_apply_all_filters_together(db, fts_enabled):
    if fts_enabled and not db._library_fts_ready:
        pytest.skip("SQLite was built without FTS5")
    db._library_fts_ready = fts_enabled
    add_paper(db, 1, tags=["ML"], year=2024, category="CS/Graph", read_status="read")
    add_paper(db, 2, tags=["ML"], year=2024, category="CS", read_status="read")
    add_paper(db, 3, tags=["ML"], year=2024, category="CS", read_status="unread")
    add_paper(db, 4, tags=["ML"], year=2020, category="CS", read_status="read")
    add_paper(db, 5, tags=["ML"], year=2024, category="Biology", read_status="read")
    add_paper(db, 6, tags=["Other"], year=2024, category="CS", read_status="read")
    add_paper(db, 7, tags=["ML"], year=2024, category="CS", read_status="read", user_id=2)
    add_paper(db, 8, tags=["ML"], year=2024, category="CS", read_status="read", title="Unrelated")
    add_paper(db, 9, tags=["ML"], year=None, category="CS", read_status="read")
    filters = dict(
        query="Graph", tags=["ML"], year_from=2023, year_to=2025,
        read_status="read", category="CS/*", user_id=1,
    )
    assert [p.id for p in db.search_library(**filters, limit=1)] == [2]
    assert [p.id for p in db.search_library(**filters, limit=1, offset=1)] == [1]
    assert db.count_search_library(**filters) == 2


def test_uncategorized_count_retains_folder_semantics(db):
    for paper_id, category in enumerate([None, "", "未分类", "未分类/Other", "CS"], start=1):
        add_paper(db, paper_id, category=category)
    assert db.count_search_library(category="未分类", user_id=1) == 3
    assert db.count_search_library(category="未分类/*", user_id=1) == 4


@pytest.mark.parametrize("offset", [0, 2, 10])
def test_library_response_reports_exact_filtered_total_on_every_page(db, offset):
    for paper_id in range(1, 5):
        add_paper(db, paper_id, tags=["Graph"], read_status="read")
    add_paper(db, 5, tags=["Other"], read_status="read")
    result = get_library(
        db=db,
        litpaper_to_api_paper_fn=lambda p: ApiPaper(id=p.id, title=p.title, tags=p.tags),
        tags="Graph", read_status=ReadStatus.READ, user_id=1, limit=2, offset=offset,
    )
    assert result.total == 4
    assert [p.id for p in result.papers] == {0: [4, 3], 2: [2, 1], 10: []}[offset]


def test_unfiltered_pages_have_stable_order_when_creation_times_tie(db):
    for paper_id in range(1, 5):
        add_paper(db, paper_id)
    assert [p.id for p in db.get_all_papers(user_id=1, limit=2)] == [4, 3]
    assert [p.id for p in db.get_all_papers(user_id=1, limit=2, offset=2)] == [2, 1]
