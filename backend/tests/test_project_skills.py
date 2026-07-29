from __future__ import annotations

from pathlib import Path

from app.services.llm.agent_config import (
    apply_project_skills,
    get_project_skill_loader,
    papergraph_agent_config,
    resolve_task_skills,
)


EXPECTED_SKILLS = {
    "academic-search",
    "knowledge-graph",
    "literature-quality",
    "paper-reader",
}


def test_project_skill_directory_is_single_source_of_truth():
    skills_dir = Path(__file__).resolve().parent.parent / "skills"

    assert skills_dir.name == "skills"
    assert skills_dir.parent.name == "backend"
    assert {path.parent.name for path in skills_dir.glob("*/SKILL.md")} == EXPECTED_SKILLS


def test_all_project_skills_have_loadable_metadata_and_body():
    for name in EXPECTED_SKILLS:
        body = apply_project_skills("", [name])
        assert f"## Project skill: {name}" in body
        assert len(body) > 200


def test_explicit_skill_injection_is_selective_and_preserves_prompt():
    prompt = apply_project_skills("Base system prompt", ["academic-search"])

    assert prompt.startswith("Base system prompt")
    assert "## Project skill: academic-search" in prompt
    assert "## Project skill: paper-reader" not in prompt


def test_skill_loader_and_rendered_bodies_are_cached():
    loader = get_project_skill_loader()
    first = apply_project_skills("", ["academic-search", "literature-quality"])
    second = apply_project_skills("", ["academic-search", "literature-quality"])

    assert get_project_skill_loader() is loader
    assert first == second


def test_task_skill_mapping_is_centralized_and_explicit_override_wins():
    assert resolve_task_skills("paper_ranker_fine_rank") == (
        "academic-search",
        "literature-quality",
    )
    assert resolve_task_skills("memory_extract") == ()
    assert resolve_task_skills("paper_ranker_fine_rank", ["paper-reader", "paper-reader"]) == (
        "paper-reader",
    )


def test_framework_skill_tool_is_disabled_to_keep_one_injection_path():
    assert papergraph_agent_config().skills_enabled is False


def test_unknown_or_empty_skills_do_not_change_prompt():
    assert apply_project_skills("Base", None) == "Base"
    assert apply_project_skills("Base", ["unknown-skill"]) == "Base"
