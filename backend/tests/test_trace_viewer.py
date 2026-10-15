"""Tests for the trace viewer (read side of hello-agents trace files)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm import trace_viewer


def _seed_trace(trace_dir: Path, session_id: str, events: list[dict]) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    with (trace_dir / f"trace-{session_id}.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (trace_dir / f"trace-{session_id}.html").write_text("<html>trace</html>", encoding="utf-8")


@pytest.fixture()
def trace_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "traces"
    monkeypatch.setattr(trace_viewer, "_default_trace_dir", lambda: d)
    return d


def test_list_sessions_reads_summary(trace_dir):
    _seed_trace(trace_dir, "s-20260805-120000-ab12", [
        {"ts": "2026-08-05T12:00:00", "session_id": "s-20260805-120000-ab12", "event": "session_start",
         "payload": {"agent_name": "papergraph_paper_reader"}},
        {"ts": "2026-08-05T12:00:01", "session_id": "s-20260805-120000-ab12", "event": "llm_call", "payload": {}},
    ])
    sessions = trace_viewer.list_trace_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "s-20260805-120000-ab12"
    assert s["event_count"] == 2
    assert s["agent_name"] == "papergraph_paper_reader"
    assert s["has_html"] is True


def test_read_events_roundtrip(trace_dir):
    _seed_trace(trace_dir, "s-20260805-120000-cd34", [
        {"event": "session_start", "payload": {}},
        {"event": "agent_finish", "payload": {"answer": "x"}},
    ])
    events = trace_viewer.read_trace_events("s-20260805-120000-cd34")
    assert events is not None and len(events) == 2
    assert events[1]["event"] == "agent_finish"


def test_invalid_or_unknown_session_rejected(trace_dir):
    _seed_trace(trace_dir, "s-20260805-120000-ef56", [{"event": "x", "payload": {}}])
    assert trace_viewer.read_trace_events("s-20260805-120000-ef56") is not None
    # path traversal / malformed ids are rejected before any file access
    assert trace_viewer.read_trace_events("../../etc/passwd") is None
    assert trace_viewer.read_trace_events("not-a-session") is None
    assert trace_viewer.read_trace_events("s-00000000-000000-ffff") is None  # unknown
    assert trace_viewer.trace_html_path("../../x") is None


def test_html_path(trace_dir):
    _seed_trace(trace_dir, "s-20260805-120000-aa99", [{"event": "x", "payload": {}}])
    p = trace_viewer.trace_html_path("s-20260805-120000-aa99")
    assert p is not None and p.name.endswith(".html")
