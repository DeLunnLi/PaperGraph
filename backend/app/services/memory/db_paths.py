"""Single source of truth for the self-evolving memory DB locations."""

from __future__ import annotations

import os

from ...settings import get_settings

READER_MEMORY_DB_NAME = "reader_memory.db"
AGENT_MEMORY_DB_NAME = "agent_memory.db"


def memory_db_paths() -> dict[str, str]:
    """Return ``{"reader": ..., "agent": ...}`` absolute DB paths."""
    root = str(get_settings().data_dir)
    return {
        "reader": os.path.join(root, READER_MEMORY_DB_NAME),
        "agent": os.path.join(root, AGENT_MEMORY_DB_NAME),
    }
