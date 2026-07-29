"""Tests for source_common.safe_int — locks in the round-1 helper that replaced
5 hand-written try/except int() parses across search sources (year/citations).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.search.sources.source_common import safe_int


def test_safe_int_none_returns_default():
    assert safe_int(None) is None
    assert safe_int(None, default=0) == 0


def test_safe_int_valid_int_string():
    assert safe_int("2024") == 2024
    assert safe_int(2024) == 2024


def test_safe_int_non_numeric_returns_default():
    assert safe_int("abc") is None
    assert safe_int("") is None
    assert safe_int("abc", default=0) == 0


def test_safe_int_float_string_returns_default():
    # int("2024.0") raises ValueError — safe_int falls back, pinning current behavior.
    assert safe_int("2024.0") is None


def test_safe_int_float_value_truncates():
    assert safe_int(2024.0) == 2024
    assert safe_int(3.7) == 3
