"""Read side of the hello-agents trace files (sessions list + event replay).

Traces were write-only observability (cleaned periodically by trace_cleanup);
this module makes them inspectable for debugging judge/reader decisions.
Session ids are validated against the strict trace filename pattern before
any file access (path-traversal safe).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .trace_cleanup import _collect_sessions, _default_trace_dir, _session_mtime

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^s-\d{8}-\d{6}-[0-9a-f]+$")


def _valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id or ""))


def list_trace_sessions(*, limit: int = 100) -> list[dict[str, Any]]:
    """Newest-first session summaries: id, last activity, event count, agent.

    Cheap stat-sort first; only the newest ``limit`` sessions get their JSONL
    parsed (the directory can hold up to trace_max_files sessions).
    """
    sessions = _collect_sessions(_default_trace_dir())
    ordered = sorted(
        sessions.items(),
        key=lambda kv: -_session_mtime(kv[1]),
    )[: max(1, int(limit))]
    out: list[dict[str, Any]] = []
    for sid, files in ordered:
        jsonl = files.get("jsonl")
        if jsonl is None:
            continue
        event_count = 0
        agent_name = ""
        try:
            with jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event_count += 1
                    if not agent_name:
                        try:
                            ev = json.loads(line)
                            if ev.get("event") == "session_start":
                                agent_name = str((ev.get("payload") or {}).get("agent_name") or "")
                        except (TypeError, json.JSONDecodeError):
                            continue
        except OSError:
            continue
        out.append({
            "session_id": sid,
            "updated_at": _session_mtime(files),
            "event_count": event_count,
            "agent_name": agent_name,
            "has_html": files.get("html") is not None,
        })
    return out


def read_trace_events(session_id: str, *, limit: int = 500) -> list[dict[str, Any]] | None:
    """Parse a session's JSONL events. None when the id is invalid/unknown."""
    if not _valid_session_id(session_id):
        return None
    sessions = _collect_sessions(_default_trace_dir())
    files = sessions.get(session_id)
    if not files or files.get("jsonl") is None:
        return None
    events: list[dict[str, Any]] = []
    try:
        with files["jsonl"].open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except (TypeError, json.JSONDecodeError):
                    continue
                if len(events) >= max(1, int(limit)):
                    break
    except OSError:
        return None
    return events


def trace_html_path(session_id: str) -> Path | None:
    """The hello-agents-rendered HTML viewer file for a session, if present."""
    if not _valid_session_id(session_id):
        return None
    sessions = _collect_sessions(_default_trace_dir())
    files = sessions.get(session_id)
    if not files:
        return None
    return files.get("html")
