
from __future__ import annotations

import time
from typing import Any

import os
from .sqlite_document_store_compat import SQLiteDocumentStore

def _memory_db_path() -> str:
    from ...settings import get_settings
    root = get_settings().data_dir
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "papers.db")

def _agent_user_id(agent_name: str) -> str:
    return f"papergraph:agent:{str(agent_name or 'unknown').strip()[:48]}"

def _shared_user_id() -> str:
    return "papergraph:shared"

class AgentMemory:

    def __init__(self, *, db_path: str | None = None) -> None:
        self.db_path = db_path or _memory_db_path()
        self.store = SQLiteDocumentStore(db_path=self.db_path)

    def add(self, *, agent_name: str, content: str, memory_type: str = "working",
            importance: float = 0.5, shared: bool = False, meta: dict[str, Any] | None = None) -> str:
        now = int(time.time())
        uid = _shared_user_id() if shared else _agent_user_id(agent_name)
        mt = str(memory_type or "working").strip()[:32] or "working"
        c = str(content or "").strip()[:420]
        if not c:
            return ""
        props: dict[str, Any] = {"shared": bool(shared), "agent": str(agent_name or "")[:48]}
        if meta:
            props.update(meta)
        return self.store.add_memory(
            memory_id=f"{uid}:{mt}:{now}:{int(float(importance or 0.5)*1000)}",
            user_id=uid, content=c, memory_type=mt, timestamp=now,
            importance=float(importance or 0.0), properties=props)

    def recent(self, *, agent_name: str, memory_types: list[str], limit: int, shared: bool) -> list[str]:
        uid = _shared_user_id() if shared else _agent_user_id(agent_name)
        out: list[tuple[int, str]] = []
        for mt in [str(x).strip() for x in (memory_types or []) if str(x).strip()]:
            for r in (self.store.search_memories(user_id=uid, memory_type=mt, limit=int(limit)) or []):
                if not isinstance(r, dict):
                    continue
                c = str(r.get("content") or "").strip()
                if c:
                    out.append((int(r.get("timestamp") or 0), c))
        return [c for _, c in sorted(out, key=lambda x: -x[0])[:max(1, int(limit))]]

    def build_context_block(self, *, agent_name: str, query: str | None = None) -> str:
        pref = self.get_preferences()
        agent_lines = self.recent(agent_name=agent_name, memory_types=["working"], limit=12, shared=False)
        agent_epi = self.recent(agent_name=agent_name, memory_types=["episodic"], limit=24, shared=False)
        lines: list[str] = []
        if pref:
            lines.append(f"【用户偏好（LLM 提炼）】\n{pref}")
        if agent_lines:
            lines.append(f"【{agent_name} 独立记忆（近期）】\n" + "\n".join(f"- {x}" for x in reversed(agent_lines)))
        if agent_epi:
            lines.append(f"【{agent_name} 独立记忆（长期）】\n" + "\n".join(f"- {x}" for x in reversed(agent_epi[:10])))
        return "\n\n".join(lines) if lines else ""

    def get_preferences(self) -> str:
        rows = self.store.search_memories(user_id=_shared_user_id(), memory_type="preference", limit=1)
        for r in (rows or []):
            c = str((r if isinstance(r, dict) else {}).get("content") or "").strip()
            if c:
                return c
        return ""

    def keywords_from_shared(self, *, limit_lines: int = 40, tokens_cap: int = 120) -> set[str]:
        from ...utils.common import tokenize_for_keywords
        lines = self.recent(agent_name="shared", memory_types=["working", "episodic"], limit=max(1, int(limit_lines)), shared=True)
        if not lines:
            return set()
        out: set[str] = set()
        for ln in lines:
            for w in tokenize_for_keywords(str(ln), min_len=3, max_len=26):
                if len(out) >= int(tokens_cap):
                    break
                out.add(w.lower())
            if len(out) >= int(tokens_cap):
                break
        return out

_agent_memory_singleton: AgentMemory | None = None

def get_agent_memory() -> AgentMemory:
    global _agent_memory_singleton
    if _agent_memory_singleton is None:
        _agent_memory_singleton = AgentMemory()
    return _agent_memory_singleton
