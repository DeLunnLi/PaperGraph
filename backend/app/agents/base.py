from __future__ import annotations

import logging
from typing import Any

from ..services.llm.llm_service import get_llm
from ..settings import get_settings

logger = logging.getLogger(__name__)

class BaseAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self.llm = self._init_llm()

    def _init_llm(self) -> Any:
        try:
            return get_llm()
        except Exception as e:
            logger.exception("[%s] LLM 初始化失败", type(self).__name__)
            raise RuntimeError(f"{type(self).__name__}_llm_init_failed") from e

    def _cfg(self, name: str, default: Any = None) -> Any:
        return getattr(self._settings, name, default)

    def _cfg_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self._cfg(name, default))
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _clip(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    # ── Shared memory access ──────────────────────────────────────
    # All agents can read/write the shared memory pool via these helpers.
    # Shared memory uses user_id="papergraph:shared", readable by any agent.
    def _get_shared_memory(self) -> Any:
        """Get the global AgentMemory singleton."""
        try:
            from ..services.memory.agent_memory import get_agent_memory
            return get_agent_memory()
        except Exception:
            return None

    def _read_shared_context(self, *, query: str | None = None, agent_name: str | None = None,
                           tags: list[str] | None = None) -> str:
        """Read shared cross-agent memory as a context block.

        Args:
            tags: Filter shared memories by action tags (e.g. ["reader", "search"]).
                  Only memories with at least one matching tag are returned.
        """
        am = self._get_shared_memory()
        if not am:
            return ""
        try:
            name = agent_name or type(self).__name__
            return am.build_context_block(agent_name=name, query=query, tags=tags) or ""
        except Exception:
            logger.debug("[%s] read_shared_context failed", type(self).__name__, exc_info=True)
            return ""

    def _read_shared_recent(self, *, memory_types: list[str] | None = None, limit: int = 8,
                            tags: list[str] | None = None) -> list[str]:
        """Read recent shared memories directly (raw list, not formatted block).

        Args:
            tags: Filter by action tags for selective sharing.
        """
        am = self._get_shared_memory()
        if not am:
            return []
        try:
            types = memory_types or ["working", "episodic"]
            return am.recent(agent_name="shared", memory_types=types, limit=limit, shared=True, tags=tags)
        except Exception:
            return []

    def _write_shared(self, *, content: str, memory_type: str = "working", importance: float = 0.5,
                     agent_name: str | None = None, tags: list[str] | None = None) -> None:
        """Write a memory to the shared pool, visible to all agents.

        Args:
            tags: Action tags for selective sharing (e.g. ["search"], ["reader"]).
        """
        am = self._get_shared_memory()
        if not am:
            return
        try:
            name = agent_name or type(self).__name__
            am.add(agent_name=name, content=content, memory_type=memory_type,
                   importance=importance, shared=True, tags=tags)
        except Exception:
            logger.debug("[%s] write_shared failed", type(self).__name__, exc_info=True)
