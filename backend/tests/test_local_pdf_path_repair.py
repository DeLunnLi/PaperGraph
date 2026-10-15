"""Tests for local_pdf_path corruption detection + repair.

Locks the root-cause fix for the pre-existing data-integrity bug where some
papers' ``local_pdf_path`` column held ``json.dumps(keywords)`` strings
(e.g. ``'["cs.LG", "cs.AI"]'``) instead of a relative file path. The reader
could not find the on-disk PDF for those papers, so ``[pN]`` citation
anchoring silently fell back to abstract-only answers.

Three layered defects fixed together:
  * detection gap — ``paper_ids_missing_local_pdf`` only flagged NULL/empty;
  * wrong scan convention — repair walked for ``<id>.pdf`` files, but the
    library actually names files ``<title>_<sha256(pid)[:6]>.pdf``;
  * no cleansing — corrupt values with no on-disk PDF were left as garbage.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.paper_paths import library_pdf_relative_path  # noqa: E402


@pytest.fixture
def db(tmp_path):
    from app.core.storage import PaperDatabase

    return PaperDatabase(str(tmp_path / "test.db"))


def _data_root(db) -> str:
    return db._data_root()


def _touch(db, relpath: str) -> str:
    """Create an empty PDF file under data_root at relpath; return relpath."""
    data_root = _data_root(db)
    full = os.path.join(data_root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(b"%PDF-1.4\n%placeholder\n")
    return relpath


def _stored_path(db, paper_id: int, user_id: int = 1) -> str | None:
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT local_pdf_path FROM papers WHERE id = ? AND user_id = ?",
            (paper_id, user_id),
        ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Detection: corrupt JSON-array values must be flagged as "missing".
# ---------------------------------------------------------------------------


def test_missing_detection_flags_corrupt_json_array(db):
    """A local_pdf_path that holds a json.dumps(keywords) string is corrupt:
    it is not a path and must be selected for repair, not just NULL/empty."""
    db._query(
        "INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)",
        (10, "corrupt arxiv cats", 1, '["cs.LG", "cs.AI", "stat.ML"]'),
    )
    db._query(
        "INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)",
        (11, "corrupt empty array", 1, "[]"),
    )
    db._query(
        "INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)",
        (12, "valid path", 1, "文献库/未分类/ok_abcdef.pdf"),
    )
    db._query(
        "INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)",
        (13, "null path", 1, None),
    )

    missing = db.paper_ids_missing_local_pdf([10, 11, 12, 13], user_id=1)
    assert set(missing) == {10, 11, 13}  # 12 has a (syntactically) valid path
    assert 11 in missing  # "[]" is corrupt, not a usable path


# ---------------------------------------------------------------------------
# Repair: re-derive the deterministic path from (category, pid, title).
# ---------------------------------------------------------------------------


def test_repair_derives_path_from_category_and_title(db):
    """Corrupt row + PDF on disk at the derived path -> repair heals it."""
    pid = 100
    title = "Reinforcing the Generation Order of Multimodal Models"
    category = "人工智能与机器学习"
    db._query(
        "INSERT INTO papers(id, title, category, user_id, local_pdf_path) "
        "VALUES(?, ?, ?, ?, ?)",
        (pid, title, category, 1, '["cs.LG", "cs.AI"]'),
    )
    expected = library_pdf_relative_path(category, pid, title)
    _touch(db, expected)
    assert os.path.isfile(os.path.join(_data_root(db), expected))

    repaired = db.repair_library_local_pdf_paths_batch([pid], user_id=1)

    assert repaired[pid] == expected
    assert _stored_path(db, pid) == expected


def test_repair_falls_back_to_hash_suffix_when_category_changed(db):
    """The id-hash suffix (sha256(pid)[:6]) is stable across renames; repair
    must find a PDF saved under a now-changed category by that suffix."""
    pid = 101
    title = "Some Paper Title"
    old_category = "旧分类"
    current_category = "新分类"
    db._query(
        "INSERT INTO papers(id, title, category, user_id, local_pdf_path) "
        "VALUES(?, ?, ?, ?, ?)",
        (pid, title, current_category, 1, "[]"),
    )
    # File lives under the OLD category dir; current category differs.
    stale_rel = library_pdf_relative_path(old_category, pid, title)
    _touch(db, stale_rel)
    # The deterministic path under the current category does NOT exist.
    assert not os.path.isfile(
        os.path.join(_data_root(db), library_pdf_relative_path(current_category, pid, title))
    )

    repaired = db.repair_library_local_pdf_paths_batch([pid], user_id=1)

    assert pid in repaired
    assert repaired[pid] == stale_rel
    assert _stored_path(db, pid) == stale_rel


def test_repair_clears_corrupt_value_when_no_pdf_on_disk(db):
    """Corrupt value + no PDF on disk -> repair must NULL it out so downstream
    code (reader) does not interpret keywords-JSON as a path."""
    pid = 102
    db._query(
        "INSERT INTO papers(id, title, category, user_id, local_pdf_path) "
        "VALUES(?, ?, ?, ?, ?)",
        (pid, "Orphan Paper", "未分类", 1, '["Information retrieval", "Question answering"]'),
    )
    # No file created.

    repaired = db.repair_library_local_pdf_paths_batch([pid], user_id=1)

    assert pid not in repaired  # nothing to point to
    assert _stored_path(db, pid) is None  # corrupt value cleared


def test_repair_leaves_valid_existing_path_untouched(db):
    """A paper whose local_pdf_path already points at an existing file is not
    reported as missing and is not touched by repair."""
    pid = 103
    rel = library_pdf_relative_path("未分类", pid, "Already Fine")
    _touch(db, rel)
    db._query(
        "INSERT INTO papers(id, title, category, user_id, local_pdf_path) "
        "VALUES(?, ?, ?, ?, ?)",
        (pid, "Already Fine", "未分类", 1, rel),
    )

    missing = db.paper_ids_missing_local_pdf([pid], user_id=1)
    assert missing == []
    repaired = db.repair_library_local_pdf_paths_batch([pid], user_id=1)
    assert repaired == {}
    assert _stored_path(db, pid) == rel


def test_repair_hash_suffix_matches_real_convention(db):
    """Sanity: the hash suffix the repair searches for equals
    sha256(str(pid))[:6] — the exact convention library_pdf_relative_path uses."""
    pid = 104
    expected_hash = hashlib.sha256(str(pid).encode()).hexdigest()[:6]
    rel = library_pdf_relative_path("未分类", pid, "Hash Check")
    assert rel.endswith(f"_{expected_hash}.pdf")
