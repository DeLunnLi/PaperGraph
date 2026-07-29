from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from hello_agents.core.config import Config
from hello_agents.skills.loader import SkillLoader

from ...settings import get_settings

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_SKILLS_DIR = _BACKEND_ROOT / "skills"

TASK_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "deep_search_synthesis": ("academic-search", "literature-quality"),
    "paper_ranker_fine_rank": ("academic-search", "literature-quality"),
    "paper_ranker_fine_rank_retry": ("academic-search", "literature-quality"),
}


@lru_cache(maxsize=1)
def get_project_skill_loader() -> SkillLoader:
    return SkillLoader(skills_dir=_PROJECT_SKILLS_DIR)


def resolve_task_skills(
    task_name: str,
    explicit: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    names = explicit if explicit is not None else TASK_SKILL_MAP.get(task_name, ())
    return tuple(dict.fromkeys(str(item).strip() for item in names if str(item).strip()))


@lru_cache(maxsize=32)
def _load_project_skills_cached(names: tuple[str, ...]) -> str:
    loader = get_project_skill_loader()
    bodies: list[str] = []
    for name in names:
        skill = loader.get_skill(name)
        if skill and skill.body:
            bodies.append(f"## Project skill: {skill.name}\n{skill.body}")
    return "\n\n".join(bodies)


def apply_project_skills(system_prompt: str, names: list[str] | tuple[str, ...] | None) -> str:
    normalized = tuple(dict.fromkeys(str(item).strip() for item in (names or ()) if str(item).strip()))
    body = _load_project_skills_cached(normalized) if normalized else ""
    if not body:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n# Project operating standards\n{body}"


def papergraph_agent_config(*, max_tokens: int | None = None) -> Config:
    memory_root = Path(get_settings().data_dir).resolve() / "memory"
    return Config(
        debug=bool(get_settings().debug),
        log_level=str(get_settings().log_level or "INFO"),
        trace_dir=str(memory_root / "traces"),
        session_dir=str(memory_root / "sessions"),
        tool_output_dir=str(memory_root / "tool-output"),
        skills_enabled=False,
        todowrite_persistence_dir=str(memory_root / "todos"),
        devlog_persistence_dir=str(memory_root / "devlogs"),
        max_tokens=max_tokens,
    )
