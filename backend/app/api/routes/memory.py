"""Memory observability endpoints: stats + list for debugging."""
from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, Depends

from ..deps import require_user
from ...services.memory.agent_memory import get_agent_memory, _shared_user_id, _agent_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory", tags=["记忆系统"])


@router.get("/stats", dependencies=[Depends(require_user)])
async def memory_stats() -> dict[str, Any]:
    """Return memory statistics: counts by type, shared vs agent."""
    am = get_agent_memory()
    return am.stats()


@router.get("/list", dependencies=[Depends(require_user)])
async def memory_list(
    scope: str = "shared",
    memory_type: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """List memories for debugging."""
    am = get_agent_memory()
    uid = _shared_user_id() if scope == "shared" else _agent_user_id(scope)
    rows = am.store.search_memories(
        user_id=uid,
        memory_type=memory_type or "",
        limit=min(int(limit), 200),
    )
    items = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        props = r.get("properties", {})
        if isinstance(props, str):
            import json
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        items.append({
            "id": str(r.get("memory_id") or r.get("id") or "")[:60],
            "type": r.get("memory_type"),
            "content": str(r.get("content") or "")[:200],
            "importance": r.get("importance"),
            "timestamp": r.get("timestamp"),
            "tags": props.get("tags", []) if isinstance(props, dict) else [],
            "agent": props.get("agent", "") if isinstance(props, dict) else "",
        })
    return {
        "scope": scope,
        "memory_type": memory_type or "all",
        "count": len(items),
        "items": items,
    }
