"""Regression test: ON CONFLICT must fire when category is NULL.

SQLite treats NULL values as distinct in UNIQUE constraints, so the original
``ON CONFLICT(date_key, keyword, category, source)`` never matched when
``category`` was None — every feedback event created a new row instead of
accumulating the interaction weight, leading to 20× row bloat and a
completely broken interest signal.
"""
from __future__ import annotations

import pytest

from app.models.schemas import FeedbackActionEnum as FA
from app.services.daily.daily_recommend_feedback import (
    _conn,
    ensure_tables,
    record_feedback,
)


@pytest.fixture()
def db_path(tmp_path) -> str:
    # tmp_path (not tempfile.mktemp) — mktemp can hand back a previously used
    # path after unlink, and ``ensure_once`` caches schema init keyed on the
    # db_path string, so a recycled path silently skips table creation and
    # pollutes unrelated tests.
    path = str(tmp_path / "papers.db")
    ensure_tables(path)
    return path


def _interest_rows(db_path: str) -> list[tuple]:
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT keyword, category, interaction_weight FROM user_interest_evolution "
            "ORDER BY keyword"
        ).fetchall()


class TestNullCategoryUpsert:
    """The category field defaults to None in the API schema."""

    def test_repeated_click_null_category_accumulates_weight(self, db_path: str) -> None:
        """Three clicks on the same keyword with no category → one row, weight 4.5."""
        for _ in range(3):
            record_feedback(
                db_path,
                user_id=1,
                date_key="2025-01-15",
                paper_identity_key="paper_a",
                identity_type="title_hash",
                title="Paper A",
                action=FA.CLICK,
                keywords=["transformer"],
                category=None,
            )
        rows = _interest_rows(db_path)
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {rows}"
        assert rows[0][0] == "transformer"
        assert rows[0][2] == pytest.approx(4.5)  # 1.5 × 3

    def test_mixed_null_and_non_null_categories(self, db_path: str) -> None:
        """NULL and non-NULL categories should produce separate rows."""
        record_feedback(
            db_path, user_id=1, date_key="2025-01-15",
            paper_identity_key="a", identity_type="title_hash",
            title="A", action=FA.CLICK, keywords=["attention"], category=None,
        )
        record_feedback(
            db_path, user_id=1, date_key="2025-01-15",
            paper_identity_key="b", identity_type="title_hash",
            title="B", action=FA.CLICK, keywords=["attention"], category="cs.CL",
        )
        record_feedback(
            db_path, user_id=1, date_key="2025-01-15",
            paper_identity_key="c", identity_type="title_hash",
            title="C", action=FA.CLICK, keywords=["attention"], category=None,
        )
        rows = _interest_rows(db_path)
        # Two rows: one for category="" (weight 3.0), one for "cs.CL" (weight 1.5)
        assert len(rows) == 2
        by_cat = {r[1]: r[2] for r in rows}
        assert by_cat[""] == pytest.approx(3.0)  # 1.5 × 2
        assert by_cat["cs.CL"] == pytest.approx(1.5)

    def test_no_row_bloat_under_realistic_traffic(self, db_path: str) -> None:
        """20 papers × 8 keywords × 30 days with no category → 240 rows, not 4800."""
        kws = ["transformer", "attention", "retrieval", "augmented",
               "generation", "graph", "neural", "alignment"]
        for day in range(30):
            dk = f"2025-01-{day + 1:02d}"
            for i in range(20):
                record_feedback(
                    db_path, user_id=1, date_key=dk,
                    paper_identity_key=f"p{day}_{i}", identity_type="title_hash",
                    title=f"Paper {i}", action=FA.CLICK, keywords=kws, category=None,
                )
        rows = _interest_rows(db_path)
        assert len(rows) == 240, f"expected 240 rows, got {len(rows)}"

    def test_different_sources_get_separate_rows(self, db_path: str) -> None:
        """Same keyword + NULL category but different source → separate rows."""
        from app.services.daily.daily_recommend_feedback import _upsert_interest_evolution

        with _conn(db_path) as conn:
            cur = conn.cursor()
            _upsert_interest_evolution(cur, "2025-01-15", "nlp", None, 1.0, "feedback", 1000)
            _upsert_interest_evolution(cur, "2025-01-15", "nlp", None, 2.0, "reading", 2000)
        rows = _interest_rows(db_path)
        by_src = {}
        with _conn(db_path) as conn:
            for r in conn.execute(
                "SELECT keyword, source, interaction_weight FROM user_interest_evolution"
            ).fetchall():
                by_src[r[1]] = r[2]
        assert len(rows) == 2
        assert by_src["feedback"] == pytest.approx(1.0)
        assert by_src["reading"] == pytest.approx(2.0)
