"""Shared task specification for PaperAnalysisAgent's single-shot LLM tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class TaskSpec:
    name: str
    system_prompt: str
    parser: Optional[Callable[[str], Any]] = None
    max_chars: int = 6000
