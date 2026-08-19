"""Tests for lib.crew.writer.prompts -- the strategist prompt, focused on the
optional reference-photo-collection section and the `ContentBrief` field it
fills.

The strategist prompt had no test module of its own; the sibling
`test_crew_writer_context.py` covers the data summarizers it interpolates,
not the prompt text itself.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.crew.writer.models import ContentBrief
from lib.crew.writer.prompts import build_strategist_task_description

_IDEA = {
    "topic": "Bone broth for dogs",
    "target_keyword": "bone broth for dogs",
    "category": "Nutrition",
    "persona_context": "Winter coat question keeps coming up.",
}


def _description(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "idea": _IDEA,
        "identity": "A dog food blog.",
        "voice": "Warm, honest, data-driven.",
        "mascot_facts": "Shepherd mix, ~50 lb.",
        "link_candidates": [],
        "year": 2026,
    }
    kwargs.update(overrides)
    return build_strategist_task_description(**kwargs)  # type: ignore[arg-type]


# ── baseline: the prompt still carries its inputs ────────────────────────


def test_strategist_prompt_carries_idea_voice_and_year() -> None:
    description = _description()
    assert "Bone broth for dogs" in description
    assert "Warm, honest, data-driven." in description
    assert "Shepherd mix, ~50 lb." in description
    assert "2026" in description


# ── reference_category (optional reference-photo library) ────────────────


def test_content_brief_parses_payload_without_reference_category() -> None:
    """Back-compat: briefs are re-parsed from LLM JSON every run and never
    persisted, so a model that omits the new field must still validate."""
    brief = ContentBrief.model_validate(
        {
            "suggested_title": "Bone Broth for Dogs in 2026",
            "primary_keyword": "bone broth for dogs",
            "mascot_angle": "She drinks it all winter.",
        }
    )
    assert brief.reference_category == ""


def test_content_brief_keeps_reference_category_when_the_model_sets_one() -> None:
    brief = ContentBrief.model_validate(
        {
            "suggested_title": "Bone Broth for Dogs in 2026",
            "primary_keyword": "bone broth for dogs",
            "mascot_angle": "She drinks it all winter.",
            "reference_category": "Eating",
        }
    )
    assert brief.reference_category == "Eating"


def test_strategist_prompt_omits_reference_section_without_a_library() -> None:
    """A brand with no reference-photo collections gets the prompt it always
    got -- the section is not rendered at all, not rendered empty."""
    description = _description()
    assert "reference_category" not in description
    assert "Reference-photo collection" not in description


def test_strategist_prompt_lists_reference_categories_when_given() -> None:
    description = _description(reference_categories=("Eating", "Outdoors", "Portrait"))
    assert "## Reference-photo collection (`reference_category`)" in description
    assert "- Eating" in description
    assert "- Outdoors" in description
    assert "- Portrait" in description
    # The no-clean-match rule is spelled out rather than left to the model.
    assert "CLOSEST" in description
    assert "copied verbatim from the list above" in description


def test_strategist_prompt_never_offers_an_unlisted_catch_all() -> None:
    """The live bug: the section closed with *if none of them fits, use
    "general"* -- naming a collection that was not in the list it had just
    supplied, and which therefore resolves to no reference photo at all."""
    description = _description(reference_categories=("forest-trail", "studio-mascot"))
    assert "general" not in description.lower()


def test_strategist_prompt_offers_a_catch_all_the_brand_actually_has() -> None:
    """A brand that keeps a stocked catch-all still gets it offered, spelled
    exactly as supplied -- the model is told to copy these verbatim."""
    description = _description(reference_categories=("forest-trail", "General"))
    assert '"General"' in description
