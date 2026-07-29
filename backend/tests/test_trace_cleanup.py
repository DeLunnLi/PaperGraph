"""trace_cleanup 测试 —— 锁定 retention 行为。

用临时目录建假 trace-*.jsonl/.html 文件，验证：
- age 策略：超 max_age_days 的会话成对删除
- count 策略：超 max_files 保留最新，删最旧
- 成对删除：jsonl+html 同时删，无孤儿
- 容错：单文件删除失败不中断
- 空目录/non-trace 文件不误删
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm.trace_cleanup import cleanup_traces, _collect_sessions


def _sid(i: int) -> str:
    """生成符合 hello-agents session_id 格式的 id：s-YYYYMMDD-HHMMSS-hex。"""
    return f"s-2026010{i}-120000-{i:04x}"


def _make_session(trace_dir: Path, sid: str, *, age_days: float = 0) -> None:
    """建一对 trace 文件（真实 session_id 格式），mtime 设为 age_days 天前。"""
    jsonl = trace_dir / f"trace-{sid}.jsonl"
    html = trace_dir / f"trace-{sid}.html"
    jsonl.write_text('{"event":"session_start"}')
    html.write_text("<html></html>")
    ts = time.time() - age_days * 86400
    os.utime(jsonl, (ts, ts))
    os.utime(html, (ts, ts))


def test_collect_sessions_pairs_jsonl_html(tmp_path):
    _make_session(tmp_path, _sid(1))
    _make_session(tmp_path, _sid(2))
    sessions = _collect_sessions(tmp_path)
    assert set(sessions) == {_sid(1), _sid(2)}
    assert sessions[_sid(1)]["jsonl"] is not None
    assert sessions[_sid(1)]["html"] is not None


def test_age_strategy_deletes_old_sessions(tmp_path):
    _make_session(tmp_path, _sid(1), age_days=10)
    _make_session(tmp_path, _sid(2), age_days=1)
    stats = cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    assert stats["deleted_sessions"] == 1
    assert stats["sessions_after"] == 1
    assert not (tmp_path / f"trace-{_sid(1)}.jsonl").exists()
    assert not (tmp_path / f"trace-{_sid(1)}.html").exists()  # 成对删除
    assert (tmp_path / f"trace-{_sid(2)}.jsonl").exists()


def test_count_strategy_keeps_newest_n(tmp_path):
    # 5 个会话，max_files=2 → 保留最新 2 个
    for i in range(5):
        _make_session(tmp_path, _sid(i), age_days=10 - i)  # i=0 最旧，i=4 最新
    stats = cleanup_traces(trace_dir=tmp_path, max_age_days=365, max_files=2)
    assert stats["deleted_sessions"] == 3
    assert stats["sessions_after"] == 2
    # 保留最新的 _sid(3), _sid(4)
    assert (tmp_path / f"trace-{_sid(4)}.jsonl").exists()
    assert (tmp_path / f"trace-{_sid(3)}.jsonl").exists()
    assert not (tmp_path / f"trace-{_sid(2)}.jsonl").exists()
    assert not (tmp_path / f"trace-{_sid(0)}.jsonl").exists()


def test_pair_deletion_no_orphans(tmp_path):
    """删除会话时 jsonl+html 必须同时删，不留孤儿。"""
    _make_session(tmp_path, _sid(1), age_days=10)
    cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    remaining = list(tmp_path.glob("trace-*"))
    assert remaining == [], f"应全部删除，剩 {remaining}"


def test_non_trace_files_not_touched(tmp_path):
    """非 trace-* 文件不误删。"""
    _make_session(tmp_path, _sid(1), age_days=10)
    other = tmp_path / "notes.txt"
    other.write_text("keep me")
    cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    assert other.exists()
    assert other.read_text() == "keep me"


def test_trace_prefixed_but_non_session_files_not_deleted(tmp_path):
    """trace- 前缀但非 session 格式的文件（如 trace-backup.jsonl）不被误删。

    回归保护：旧版宽松匹配会误删这类文件，收紧正则后必须跳过。
    """
    _make_session(tmp_path, _sid(1), age_days=10)  # 真会话，会被删
    backup = tmp_path / "trace-backup-important.jsonl"
    backup.write_text("do not delete")
    backup_html = tmp_path / "trace-manual.html"
    backup_html.write_text("<html>manual</html>")
    cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    assert backup.exists(), "trace-backup-*.jsonl 不应被当作 trace 删除"
    assert backup_html.exists(), "trace-manual.html 不应被当作 trace 删除"
    assert not (tmp_path / f"trace-{_sid(1)}.jsonl").exists(), "真会话应被删"


def test_empty_dir_returns_zero(tmp_path):
    stats = cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    assert stats == {"sessions_before": 0, "sessions_after": 0, "deleted_sessions": 0, "deleted_files": 0}


def test_nonexistent_dir_returns_zero(tmp_path):
    stats = cleanup_traces(trace_dir=tmp_path / "does-not-exist", max_age_days=7, max_files=1000)
    assert stats["deleted_sessions"] == 0


def test_age_and_count_combined(tmp_path):
    """age 先删旧，count 再截断剩余。"""
    # 3 个超 age（10 天）+ 3 个新（1 天），max_files=2
    for i in range(3):
        _make_session(tmp_path, _sid(i), age_days=10)
    for i in range(3, 6):
        _make_session(tmp_path, _sid(i), age_days=1)
    stats = cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=2)
    # age 删 3 个 old（_sid 0-2），剩 3 个 new（_sid 3-5），count 截断到 2 → 再删 1 个
    assert stats["deleted_sessions"] == 4
    assert stats["sessions_after"] == 2
    remaining = list(tmp_path.glob("trace-s-*.jsonl"))
    assert len(remaining) == 2


def test_no_deletion_under_limits(tmp_path):
    """文件数和 age 都在限额内 → 不删。"""
    _make_session(tmp_path, _sid(1), age_days=1)
    _make_session(tmp_path, _sid(2), age_days=2)
    stats = cleanup_traces(trace_dir=tmp_path, max_age_days=7, max_files=1000)
    assert stats["deleted_sessions"] == 0
    assert stats["sessions_after"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
