"""Which photo a retry anchors on -- `lib.crew.socialpost.retry`.

The failure this exists for, verbatim from the 2026-08-20 05:58 run::

    reference_library_no_match  available=forest-trail,home-exterior,... requested=(none)
    social_posts_reference_unmatched  reason=no_library_match  requested_category=

The planner emitted an EMPTY `reference_category`. It matches nothing,
generation is skipped, and the WP hero ships. That is DETERMINISTIC, so the
headline test here is not "a retry runs" but "a retry with the same empty
category reaches a real photo anyway" -- a retry wired to the composed
resolution would reproduce the fallback every time and the button would be
theatre.

What a retry is allowed to TOUCH is `test_socialpost_retry_safety.py`.

Real files under a `tmp_path` brand dir and the real reference resolver; the
crew, the image model, the overlay pass and Postgres are faked
(`tests/_socialpost_retry_fakes.py`).
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.crew.socialpost import retry
from tests._reference_library_fakes import write_library
from tests._socialpost_retry_fakes import (
    IDEA_ID,
    OLD_IMAGE,
    OLD_IMAGE_BYTES,
    build_world,
    plan_for,
    run_retry,
)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return build_world(monkeypatch, tmp_path)


# ── the headline: an empty category no longer means "no photo" ───────────────


def test_an_empty_plan_category_still_reaches_a_real_mascot_photo(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """THE REGRESSION. This is exactly the live fallback: the planner named no
    collection, so `resolve_reference` matches nothing. Composition stops there
    and ships the hero. A retry must not -- it falls through to any photo tagged
    `shows_mascot`, and generates."""
    write_library(
        tmp_path,
        {"home-exterior": "porch-bytes", "studio-mascot": "mascot-bytes"},
        mascot_categories=("studio-mascot",),
    )
    world["plan"] = plan_for("")  # what the planner actually emitted

    result = run_retry(world)

    assert result.ok is True
    assert result.reason == "regenerated"
    assert world["generate_calls"][0]["reference_image_bytes"] == b"mascot-bytes"
    assert result.reference_id == "studio-mascot/mascot-bytes.png"
    assert result.source == "gemini"


def test_a_library_with_no_mascot_photo_changes_nothing(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """The one case a retry genuinely cannot help: nothing may be substituted
    for a photo of the mascot the brand never uploaded. The post is left exactly
    as it was -- old image on disk, row put back in the queue, no generation."""
    write_library(tmp_path, {"home-exterior": "porch-bytes"})
    world["plan"] = plan_for("")

    result = run_retry(world)

    assert (result.ok, result.reason) == (False, "no_reference_photo")
    assert world["generate_calls"] == []
    assert world["written"] == []
    assert world["restored"] == [IDEA_ID]
    assert (tmp_path / OLD_IMAGE).read_bytes() == OLD_IMAGE_BYTES


# ── which photo a retry anchors on ──────────────────────────────────────────


def test_the_operator_pick_beats_the_plan(world: dict[str, Any], tmp_path: Path) -> None:
    """The operator can see the post and the library; the planner that produced
    the fallback could see neither."""
    write_library(
        tmp_path,
        {"home-exterior": "porch-bytes", "forest-trail": "trail-bytes"},
    )
    world["plan"] = plan_for("home-exterior")

    result = run_retry(world, reference_category="forest-trail")

    assert result.ok is True
    assert world["generate_calls"][0]["reference_image_bytes"] == b"trail-bytes"


def test_a_resolvable_plan_category_is_still_used(world: dict[str, Any], tmp_path: Path) -> None:
    """The plan is not the enemy -- an unmatched tag is. When it names something
    the library holds and the operator picked nothing, it wins."""
    write_library(tmp_path, {"home-exterior": "porch-bytes"}, mascot_categories=())
    world["plan"] = plan_for("home-exterior")

    run_retry(world)

    assert world["generate_calls"][0]["reference_image_bytes"] == b"porch-bytes"


def test_a_retry_does_not_pick_the_same_photo_twice(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`resolve_reference`'s pick is seeded and reproducible, so a seed of the
    idea id alone would hand back the photo the operator just rejected. The
    per-attempt stamp is what makes a second click a second outcome."""
    write_library(
        tmp_path,
        {"studio-mascot": "mascot-a", "forest-trail": "mascot-b"},
        shows_mascot=True,
    )
    world["plan"] = plan_for("")

    picked: list[bytes] = []
    # Two stamps that land on different indices of the two-photo candidate list
    # -- the property under test is that the seed CARRIES the stamp, and a pair
    # that happened to collide would prove nothing either way.
    for stamp in ("stamp-a", "stamp-b"):
        monkeypatch.setattr(retry, "_stamp", lambda s=stamp: s)
        world["generate_calls"].clear()
        run_retry(world)
        picked.append(world["generate_calls"][0]["reference_image_bytes"])

    assert picked[0] != picked[1]


def test_the_mascot_anchor_still_rides_along(world: dict[str, Any], tmp_path: Path) -> None:
    """A retry is not a different kind of image: a scene photo that cannot carry
    the mascot still gets the mascot photo attached as PHOTO 2."""
    write_library(
        tmp_path,
        {"home-exterior": "porch-bytes", "studio-mascot": "mascot-bytes"},
        mascot_categories=("studio-mascot",),
    )

    run_retry(world, reference_category="home-exterior")

    call = world["generate_calls"][0]
    assert call["reference_image_bytes"] == b"porch-bytes"
    assert [p.bytes_ for p in call["extra_reference_images"]] == [b"mascot-bytes"]
    assert "PHOTO 2 -- " in call["reference_clause"]
