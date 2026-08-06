"""Trace viewer API: inspect LLM/agent trace sessions (read-only)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ...utils.common import route_errors
from ..deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/traces", tags=["追踪"])


@router.get("")
async def list_traces(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    from ...services.llm.trace_viewer import list_trace_sessions

    with route_errors("traces_list"):
        sessions = await run_in_threadpool(list_trace_sessions, limit=int(limit))
        return {"success": True, "total": len(sessions), "sessions": sessions}


@router.get("/{session_id}")
async def get_trace(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    from ...services.llm.trace_viewer import read_trace_events

    with route_errors("traces_get"):
        events = await run_in_threadpool(read_trace_events, session_id, limit=int(limit))
        if events is None:
            raise HTTPException(status_code=404, detail="trace 不存在")
        return {"success": True, "session_id": session_id, "total": len(events), "events": events}


@router.get("/{session_id}/html")
async def get_trace_html(session_id: str, user: dict = Depends(require_user)):
    from ...services.llm.trace_viewer import trace_html_path

    with route_errors("traces_html"):
        path = await run_in_threadpool(trace_html_path, session_id)
        if path is None:
            raise HTTPException(status_code=404, detail="trace HTML 不存在")
        # hello-agents writes trace payloads into the HTML unescaped; serving
        # inline would execute attacker-controlled content on the API origin.
        # Force download instead of rendering.
        return FileResponse(
            str(path),
            media_type="text/html",
            filename=f"trace-{session_id}.html",
            content_disposition_type="attachment",
        )
