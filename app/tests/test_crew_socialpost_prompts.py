"""Tests for the optional reference-photo-collection section of
`lib.crew.socialpost.prompts` and the `reference_category` field it fills.

Lives beside `test_crew_socialpost.py` rather than inside it: that module is
already past this repo's 300-line ceiling, so the new surface gets its own
file instead of pushing it further over.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.prompts import build_social_post_task_description

_PLAN_PAYLOAD = {
    "target_question": "Is bone broth safe for dogs?",
    "comment_keyword": "BROTH",
    "fb_caption": "Bone broth is safe for most dogs when made without onions.",
    "ig_caption": "Bone broth is safe for most dogs when made without onions.",
    "overlay_headline": "BONE BROTH",
    "overlay_subcopy": "The no-bones method",
    "image_brief": "A dog watching a pot simmer in a warm kitchen.",
    "cta_ribbon_text": "FULL GUIDE -> DOGFOODANDFUN.COM",
    "image_alt_text": "A dog watching a pot of bone broth simmer on the stove.",
}


def _description(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "title": "Bone Broth for Dogs",
        "body": "Body text.",
        "target_keyword": "bone broth",
        "site_domain": "dogfoodandfun.com",
        "brand_voice": "Warm, honest, data-driven.",
    }
    kwargs.update(overrides)
    return build_social_post_task_description(**kwargs)  # type: ignore[arg-type]


def test_social_post_plan_parses_payload_without_reference_category() -> None:
    """Back-compat: plans are re-parsed from LLM JSON every run and never
    persisted, so a model that omits the new field must still validate."""
    plan = SocialPostPlan.model_validate(_PLAN_PAYLOAD)
    assert plan.reference_category == ""


def test_social_post_plan_keeps_reference_category_when_the_model_sets_one() -> None:
    plan = SocialPostPlan.model_validate({**_PLAN_PAYLOAD, "reference_category": "Eating"})
    assert plan.reference_category == "Eating"


def test_social_post_prompt_omits_reference_section_without_a_library() -> None:
    """A brand with no reference-photo collections gets the prompt it always
    got -- the section is not rendered at all, not rendered empty."""
    description = _description()
    assert "reference_category" not in description
    assert "Reference-photo collection" not in description


def test_social_post_prompt_lists_reference_categories_when_given() -> None:
    description = _description(reference_categories=("Eating", "Outdoors"))
    assert "## Reference-photo collection (`reference_category`)" in description
    assert "- Eating" in description
    assert "- Outdoors" in description
    # It must tie the choice to the image brief the writer just wrote, and
    # spell out the no-clean-match rule rather than leave it to the model.
    assert "`image_brief`" in description
    assert "CLOSEST" in description
    assert "copied verbatim from the list above" in description


def test_social_post_prompt_never_offers_an_unlisted_catch_all() -> None:
    """The live bug: the section closed with *if none of them fits, use
    "general"* -- naming a collection that was not in the list it had just
    supplied, so the model's choice resolved to no image at all."""
    description = _description(reference_categories=("forest-trail", "studio-mascot"))
    assert "general" not in description.lower()


def test_social_post_prompt_offers_a_catch_all_the_brand_actually_has() -> None:
    """A brand that keeps a stocked catch-all still gets it offered, spelled
    exactly as supplied -- the model is told to copy these verbatim."""
    description = _description(reference_categories=("forest-trail", "General"))
    assert '"General"' in description
