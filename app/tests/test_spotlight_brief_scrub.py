"""`lib.crew.spotlight.brief_scrub` -- negated packaging never reaches the image model.

The three briefs under REGRESSION are the ones the writer actually produced
for the first live spotlight (WP 4741, Greenies, 2026-09-30). Each OBEYED
rule 5 by saying the packaging was absent, and each was rejected for naming
it -- two runs, five of six retries spent.
"""

# ruff: noqa: S101
from __future__ import annotations

from typing import Any

import pytest

from lib.crew.spotlight.brief_scrub import scrub_image_brief
from lib.crew.spotlight.rules import find_spotlight_violations
from tests._spotlight_fakes import build_world, plan_for, product_entry, run_compose

_SCENE = (
    "A fluffy 50 lb dark-and-tan shepherd mix sits on a kitchen floor, head tilted "
    "up toward a man's open hand holding a small dental chew."
)


@pytest.mark.parametrize(
    "absent",
    [
        "Nothing in frame carries text, labels or packaging -- just the dog, the hand, "
        "the treat and a lived-in kitchen.",
        "No jar, no labels and no packaging anywhere on the counter.",
        "There are no bags, boxes or wrappers in the shot.",
        "The counter is free of containers and logos.",
        "Nothing on the counter isn’t plain wood: no tins, no cartons.",
    ],
)
def test_regression_a_sentence_that_only_says_packaging_is_absent_is_dropped(
    absent: str,
) -> None:
    assert scrub_image_brief(f"{_SCENE} {absent}") == _SCENE


def test_an_affirmative_packaging_noun_survives_for_the_rule_to_reject() -> None:
    brief = f"{_SCENE} The treat bag sits open on the counter."
    assert scrub_image_brief(brief) == brief


def test_negation_scope_ends_at_a_contrast_so_the_bag_after_it_is_kept() -> None:
    """ "No labels visible, but a bag…" names a bag. Keeping the sentence is
    what lets rule 5 reject it."""
    sentence = "No labels are visible, but a bag leans on the cabinet."
    assert scrub_image_brief(sentence) == sentence


def test_a_dash_ends_the_negation_too() -> None:
    sentence = "Nothing is written on the bowl -- a box sits beside it."
    assert scrub_image_brief(sentence) == sentence


def test_sentences_without_packaging_are_untouched_even_when_negated() -> None:
    brief = f"{_SCENE} The dog is not looking at the camera."
    assert scrub_image_brief(brief) == brief


def test_words_that_merely_contain_a_packaging_noun_are_not_matched() -> None:
    brief = "A tiny dog stretches beside a boxwood hedge. Nothing jarring in frame."
    assert scrub_image_brief(brief) == brief


# ── rule 5 now checks the scrubbed brief ────────────────────────────────────


def _violations(image_brief: str) -> list[str]:
    return find_spotlight_violations(plan_for(image_brief=image_brief), product=product_entry())


def test_regression_the_live_brief_that_obeyed_rule_5_now_passes_it() -> None:
    brief = (
        f"{_SCENE} Warm late-afternoon light comes in from a window on the right. "
        "Nothing in frame carries text, labels or packaging -- just the dog, the hand, "
        "the treat and a lived-in kitchen."
    )
    assert _violations(brief) == []


def test_an_affirmative_bag_still_blocks() -> None:
    violations = _violations(f"{_SCENE} The treat bag sits open on the counter.")
    assert len(violations) == 1
    assert violations[0].startswith("image_brief must describe the scene only")


def test_a_brief_that_only_lists_what_is_absent_blocks() -> None:
    """Scrubbing it leaves nothing to draw -- that is a failure, not a pass."""
    violations = _violations("No labels, no packaging and no logos anywhere.")
    assert violations == [
        "image_brief must describe what IS in the frame -- the dog, the hands, "
        "the setting and the light -- not only what is absent from it."
    ]


# ── compose hands the image model the scrubbed brief ────────────────────────


def test_the_image_model_never_sees_the_negated_packaging_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Image models do not parse negation: "no labels" is a prompt that
    mentions labels. Passing the rule is not enough -- the words must not
    reach the picture either."""
    world = build_world(monkeypatch, tmp_path)
    world["plan"] = plan_for(
        image_brief=f"{_SCENE} Nothing in frame carries text, labels or packaging."
    )

    result = run_compose(world)

    assert result.ok
    sent = world["generate_calls"][0]["brief"]
    assert "shepherd mix sits on a kitchen floor" in sent
    assert "packaging" not in sent.lower()
    assert "labels" not in sent.lower()
