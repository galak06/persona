"""Tests for the persona anchor and the informed category menu.

Two defects, both seen on the same live social post (idea 92062f17, composed
2026-08-31 16:24):

1. The planner was shown only slugs, so it tagged an indoor "hold the dog's
   mouth open over a plate of kibble" brief as `home-exterior` -- a cottage
   porch. The photo resolved, was attached, and contributed nothing.
2. The same brief opened "A person holds..." and generated an anonymous
   cropped hand, because nothing anchored WHO that person is.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

import pytest

from lib.crew.reference_clauses import anchored_reference_clause
from lib.crew.reference_manifest import ReferenceImage
from lib.crew.reference_persona import brief_calls_for_a_person, persona_anchor
from lib.crew.reference_vocabulary import described_categories

_BRIEF_WITH_PERSON = "A person holds a dog's mouth open to show its teeth."
_BRIEF_WITHOUT = "A close-up of dry kibble in a ceramic bowl on a wooden floor."


def _image(
    image_id: str, *, mascot: bool = False, persona: bool = False, category: str = "general"
) -> ReferenceImage:
    return ReferenceImage(
        id=image_id,
        category=category,
        path=Path("/nonexistent") / image_id,
        content_type="image/png",
        label=image_id,
        shows_mascot=mascot,
        shows_persona=persona,
    )


# ─────────────────────────────────────────────────────────────────────────────
# brief_calls_for_a_person


@pytest.mark.parametrize(
    "brief",
    [
        _BRIEF_WITH_PERSON,
        "A man kneels beside his dog on a porch.",
        "Two hands scoop kibble into a bowl.",
        "The owner's lap with a sleeping puppy on it.",
    ],
)
def test_a_human_in_the_brief_is_detected(brief: str) -> None:
    assert brief_calls_for_a_person(brief) is True


@pytest.mark.parametrize(
    "brief",
    [
        _BRIEF_WITHOUT,
        "A dog sits alone on a forest trail at golden hour.",
        "A handful of kibble spilling from a torn bag.",
        "A management chart of feeding times.",
        "",
    ],
)
def test_no_human_means_no_persona_photo_is_forced_into_the_frame(brief: str) -> None:
    """Attaching a face unconditionally would ADD a person to images that
    should have none -- worse than the bug being fixed."""
    assert brief_calls_for_a_person(brief) is False


def test_the_personas_own_name_counts_as_a_person() -> None:
    """The one 'person word' the engine cannot know in advance."""
    brief = "Nalla's Dad checks the bowl before the morning walk."
    assert brief_calls_for_a_person(brief, "Nalla's Dad") is True
    assert brief_calls_for_a_person(brief) is False


# ─────────────────────────────────────────────────────────────────────────────
# persona_anchor


def test_no_anchor_when_the_brief_has_no_human(tmp_path: Path) -> None:
    scene = _image("home-exterior/a.png")
    assert persona_anchor(tmp_path, scene, None, brief=_BRIEF_WITHOUT) is None


def test_no_anchor_when_the_scene_photo_already_shows_the_persona(tmp_path: Path) -> None:
    scene = _image("persona-portrait/a.png", persona=True)
    assert persona_anchor(tmp_path, scene, None, brief=_BRIEF_WITH_PERSON) is None


def test_no_anchor_when_the_mascot_anchor_already_shows_the_persona(tmp_path: Path) -> None:
    """The live case: a forest-trail photo carrying BOTH subjects is already
    attached, so a third photo would only repeat the same face."""
    scene = _image("home-exterior/a.png")
    mascot = _image("forest-trail/b.jpg", mascot=True, persona=True)
    assert persona_anchor(tmp_path, scene, mascot, brief=_BRIEF_WITH_PERSON) is None


def test_no_anchor_without_a_scene_to_attach_it_to(tmp_path: Path) -> None:
    assert persona_anchor(tmp_path, None, None, brief=_BRIEF_WITH_PERSON) is None


def test_no_anchor_when_the_brand_keeps_no_persona_photo(tmp_path: Path) -> None:
    """An empty library must degrade to today's behaviour, never substitute."""
    scene = _image("home-exterior/a.png")
    mascot = _image("studio-mascot/b.png", mascot=True)
    assert persona_anchor(tmp_path, scene, mascot, brief=_BRIEF_WITH_PERSON) is None


# ─────────────────────────────────────────────────────────────────────────────
# anchored_reference_clause


def test_a_scene_that_shows_a_subject_is_not_called_a_props_reference() -> None:
    """A studio portrait of the mascot is not a 'setting, styling and props'
    photo, and saying so contradicts PHOTO 1's own identity clause."""
    scene = _image("studio-mascot/a.png", mascot=True)
    persona = _image("persona-portrait/b.png", persona=True)
    clause = anchored_reference_clause(scene, [persona], "Nalla", "dog", "Nalla's Dad")
    assert "PHOTO 1 fixes the setting and the subject it shows" in clause
    assert "styling and props" not in clause


def test_a_plain_scene_keeps_the_props_wording() -> None:
    scene = _image("home-exterior/a.png")
    anchor = _image("studio-mascot/b.png", mascot=True)
    clause = anchored_reference_clause(scene, [anchor], "Nalla", "dog", "Nalla's Dad")
    assert "PHOTO 1 fixes the setting, styling and props" in clause


def test_two_anchors_are_numbered_in_attachment_order() -> None:
    scene = _image("home-exterior/a.png")
    mascot = _image("studio-mascot/b.png", mascot=True)
    persona = _image("persona-portrait/c.png", persona=True)
    clause = anchored_reference_clause(scene, [mascot, persona], "Nalla", "dog", "Nalla's Dad")
    assert "THREE reference photos are attached" in clause
    assert clause.index("PHOTO 1 --") < clause.index("PHOTO 2 --") < clause.index("PHOTO 3 --")


def test_no_anchors_falls_back_to_the_single_reference_clause() -> None:
    scene = _image("home-exterior/a.png")
    clause = anchored_reference_clause(scene, [], "Nalla", "dog", "Nalla's Dad")
    assert "reference photos are attached" not in clause


# ─────────────────────────────────────────────────────────────────────────────
# described_categories -- the planner must see photos, not slugs


def test_each_label_is_returned_apart_from_its_description() -> None:
    """Pre-joining them invites the planner to copy the prose into the field it
    must fill with the label verbatim."""
    described = described_categories(
        ["home-exterior"], {"home-exterior": ["Wooden cottage exterior with porch."]}
    )
    assert described == [("home-exterior", "Wooden cottage exterior with porch.")]


def test_a_category_with_no_descriptions_degrades_to_its_bare_label() -> None:
    assert described_categories(["forest-trail"], {}) == [("forest-trail", "")]


def test_long_descriptions_are_truncated_rather_than_flooding_the_prompt() -> None:
    described = described_categories(
        ["studio-mascot"], {"studio-mascot": ["x" * 400]}, max_chars=40
    )
    assert len(described[0][1]) <= 40
    assert described[0][1].endswith("…")


def test_examples_are_capped_per_category() -> None:
    described = described_categories(
        ["forest-trail"], {"forest-trail": ["one.", "two.", "three."]}, max_examples=2
    )
    assert "three." not in described[0][1]
