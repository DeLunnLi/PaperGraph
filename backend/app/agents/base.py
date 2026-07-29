from __future__ import annotations

import logging
from typing import Any

from ..services.llm.llm_service import get_llm
from ..settings import get_settings
from ..utils.common import suppress_exceptions
from app.exceptions import LLMError

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
            raise LLMError(f"{type(self).__name__}_llm_init_failed",
                           code="llm_init_failed") from e

    @staticmethod
    def _clip(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    # ── Shared memory access ──────────────────────────────────────
    # All agents can read/write the shared memory pool via these helpers.
    # Shared memory uses user_id="papergraph:shared", readable by any agent.
    @suppress_exceptions(default_return=None, log_level="debug", log_message="agent_get_shared_memory_failed")
    def _get_shared_memory(self) -> Any:
        """Get the global AgentMemory singleton."""
        from ..services.memory.agent_memory import get_agent_memory
        return get_agent_memory()

    @suppress_exceptions(default_return=[], log_level="debug", log_message="agent_read_shared_recent_failed")
    def _read_shared_recent(self, *, memory_types: list[str] | None = None, limit: int = 8,
                            tags: list[str] | None = None) -> list[str]:
        """Read recent shared memories directly (raw list, not formatted block).

        Args:
            tags: Filter by action tags for selective sharing.
        """
        am = self._get_shared_memory()
        if not am:
            return []
        types = memory_types or ["working", "episodic"]
        return am.recent(agent_name="shared", memory_types=types, limit=limit, shared=True, tags=tags)

    @suppress_exceptions(default_return=None, log_level="debug", log_message="agent_write_shared_failed")
    def _write_shared(self, *, content: str, memory_type: str = "working", importance: float = 0.5,
                     agent_name: str | None = None, tags: list[str] | None = None) -> None:
        """Write a memory to the shared pool, visible to all agents.

        Args:
            tags: Action tags for selective sharing (e.g. ["search"], ["reader"]).
        """
        am = self._get_shared_memory()
        if not am:
            return
        name = agent_name or type(self).__name__
        am.add(agent_name=name, content=content, memory_type=memory_type,
               importance=importance, shared=True, tags=tags)
