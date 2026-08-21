"""The tagger asks for SPECIFIC tags, and asks about both subjects.

`tests/test_reference_vision.py` covers the call -- header auth, computed
novelty, every failure returning `None`. This file covers what the call
actually asks for, which is the thing that broke.

The measured defect: an operator uploaded ten photos -- two studio character
sheets of the mascot, two of the person behind the brand, two forest trails, a
porch, products on a deck, and two of the two of them together -- and all ten
landed in `general`. Not a model failure: the prompt said "prefer reusing a tag
from the list over inventing a near-duplicate", and `general` was the only tag
a fresh library had, so reuse WAS the instruction. One bucket holding every
kind of scene makes `resolve_reference`'s pick inside it arbitrary, which is
how a beat asking for `products` came back with a portrait.

So the assertions here are about bias, not vocabulary: a specific tag is the
expected answer, an existing tag is reused only when it describes the same kind
of scene, and `general` is explicitly a last resort. Plus the second subject --
`shows_persona`, judged independently of `shows_mascot`, because a photo may
show the person, the mascot, both, or neither, and the identity clause
downstream has to name whichever are really there.
"""
# ruff: noqa: S101

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lib.crew.reference_vision import analyze_image, vision_model
from lib.crew.reference_vision_prompt import build_prompt

_CATEGORIES = ["forest-trail", "studio-mascot", "products"]


def _url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/{vision_model()}:generateContent"
    )


def _answer(
    category: str = "forest-trail",
    *,
    mascot: bool = False,
    persona: bool = False,
) -> httpx.Response:
    payload = {
        "description": "a photo",
        "category": category,
        "shows_mascot": mascot,
        "shows_persona": persona,
    }
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


def _prompt_sent(route: respx.Route) -> str:
    return str(json.loads(route.calls[0].request.content)["contents"][0]["parts"][1]["text"])


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_VISION_MODEL", raising=False)


# ── the tag asked for is a specific one ──────────────────────────────────────


def test_the_prompt_asks_for_a_specific_tag_derived_from_the_image() -> None:
    prompt = build_prompt(["general"], "", "", "")

    assert "ONE SPECIFIC tag" in prompt
    assert "derive it from" in prompt
    # Shape guidance, so "two words max, lowercase" is not left to taste.
    assert "At most two words, lowercase" in prompt


def test_the_prompt_does_not_bias_toward_reuse() -> None:
    """THE inversion. The old wording -- "prefer reusing a tag from the list
    over inventing a near-duplicate" -- read, in a library whose only tag was
    `general`, as "put everything in general"."""
    prompt = build_prompt(["general"], "", "", "")

    assert "prefer reusing" not in prompt.lower()
    assert "a new tag is the right answer" in prompt


def test_reuse_is_conditioned_on_describing_the_same_scene() -> None:
    """The anti-duplicate rule survives -- it just guards specific tags against
    each other now instead of herding everything into a catch-all."""
    prompt = build_prompt(_CATEGORIES, "", "", "")

    assert "ONLY if it genuinely describes this same kind of scene" in prompt
    assert '"forest-path" when "forest-trail" exists' in prompt
    assert '"forest-trail", "studio-mascot", "products"' in prompt


def test_general_is_named_as_a_last_resort() -> None:
    prompt = build_prompt(["general"], "", "", "")

    assert 'Use "general" ONLY as a last resort' in prompt
    # And the reason, so the instruction is not a bare prohibition.
    assert "picked for scenes it does not match" in prompt


def test_a_brand_with_no_tags_yet_is_told_so_rather_than_shown_nothing() -> None:
    assert "(no tags yet)" in build_prompt([], "", "", "")


# ── two subjects, judged independently ───────────────────────────────────────


def test_both_flags_are_asked_for_separately() -> None:
    prompt = build_prompt([], "Nalla", "dog", "Nalla's Dad")

    assert "(2) shows_mascot" in prompt
    assert "(3) shows_persona" in prompt
    assert "INDEPENDENTLY of shows_mascot" in prompt
    assert "both subjects, either one alone, or neither" in prompt


@respx.mock
@pytest.mark.parametrize(
    ("mascot", "persona"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_every_flag_combination_round_trips(mascot: bool, persona: bool) -> None:
    """All four are real: one of the operator's photos is the person AND the
    mascot together, and neither flag may be inferred from the other."""
    respx.post(_url()).mock(return_value=_answer(mascot=mascot, persona=persona))

    analysis = analyze_image(b"png", "image/png", existing_categories=_CATEGORIES)

    assert analysis is not None
    assert (analysis.shows_mascot, analysis.shows_persona) == (mascot, persona)


@respx.mock
def test_a_missing_persona_flag_defaults_to_false() -> None:
    """An older model answer, or a refusal to fill the field, must read as
    "no persona in this photo" -- the same safe default `shows_mascot` has."""
    respx.post(_url()).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "description": "a porch",
                                            "category": "home-exterior",
                                            "shows_mascot": False,
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )

    analysis = analyze_image(b"png", "image/png", existing_categories=_CATEGORIES)

    assert analysis is not None
    assert analysis.shows_persona is False
    assert (analysis.category, analysis.is_new_category) == ("home-exterior", True)


@respx.mock
def test_the_structured_output_schema_requires_both_flags() -> None:
    """Belt and braces on the round trip above: the request itself has to ask
    for `shows_persona`, or a well-behaved model would never send one."""
    route = respx.post(_url()).mock(return_value=_answer())

    assert analyze_image(b"png", "image/png", existing_categories=[]) is not None

    schema = json.loads(route.calls[0].request.content)["generationConfig"]["responseSchema"]
    assert "shows_persona" in schema["properties"]
    assert "shows_persona" in schema["required"]


@respx.mock
def test_the_specific_tag_the_model_proposes_survives_to_the_caller() -> None:
    """The end-to-end shape of the fix: a forest photo comes back tagged
    `forest-trail`, flagged as new, and does NOT get flattened into
    `general`."""
    respx.post(_url()).mock(return_value=_answer("pine-forest"))

    analysis = analyze_image(b"png", "image/png", existing_categories=["general"])

    assert analysis is not None
    assert (analysis.category, analysis.is_new_category) == ("pine-forest", True)


def test_the_prompt_never_asks_in_terms_of_a_species_or_a_gender() -> None:
    """Same contract as `tests/test_brand_agnostic_identity.py`, asserted on
    the prompt builder directly so a future edit to this string is caught
    here rather than through a model call."""
    prompt = build_prompt(_CATEGORIES, "", "", "").lower()

    for word in ("dog", "animal", " he ", " she ", " his ", " her ", " man ", " woman "):
        assert word not in prompt
