"""OpenAlex publication_year filters must use range syntax, never >= / <=.

Regression: the filter was built as `publication_year:>=2024`, which OpenAlex
rejects with HTTP 400 ("Value for param publication_year must be a number").
Every OpenAlex search that passed only a start year therefore failed outright —
including the daily-recommendations OpenAlex fallback. The supported form is an
open-ended range: `2024-` (>=2024) and `-2024` (<=2024).
"""
from __future__ import annotations

import re

import pytest

from app.services.retrieval.source_plan import openalex_publication_year_filter as year_filter


def test_year_from_uses_open_ended_range() -> None:
    assert year_filter(2024, None) == "publication_year:2024-"


def test_year_to_uses_open_ended_range() -> None:
    assert year_filter(None, 2020) == "publication_year:-2020"


def test_closed_range() -> None:
    assert year_filter(2020, 2024) == "publication_year:2020-2024"


def test_same_year_collapses_to_exact_match() -> None:
    assert year_filter(2024, 2024) == "publication_year:2024"


def test_reversed_bounds_are_swapped() -> None:
    assert year_filter(2024, 2020) == "publication_year:2020-2024"


def test_no_bounds_returns_none() -> None:
    assert year_filter(None, None) is None


def test_non_numeric_bounds_are_ignored() -> None:
    assert year_filter("not-a-year", None) is None


@pytest.mark.parametrize(
    "args",
    [(2024, None), (None, 2020), (2020, 2024), (2024, 2024), ("x", 2024)],
)
def test_never_emits_comparison_operators(args: tuple) -> None:
    """The exact shape OpenAlex 400s on must never be produced."""
    out = year_filter(*args) or ""
    assert ">=" not in out
    assert "<=" not in out
    assert ">" not in out
    assert "<" not in out


def test_openalex_source_does_not_hardcode_comparison_filter() -> None:
    """Three call sites previously bypassed the helper with an f-string. Keep
    them routed through it so the invalid syntax cannot creep back in."""
    from pathlib import Path

    import app.core.search.sources.openalex as oa_mod

    src = Path(oa_mod.__file__).read_text(encoding="utf-8")
    # Match real interpolation (f"...publication_year:>={var}"), not prose in a
    # comment that merely documents the broken form.
    assert not re.search(r"publication_year:[<>]=\{", src)
