"""The photo that anchors the brand's mascot -- `lib.crew.reference_mascot`
and the two-photo clause it goes with (`reference_clauses`).

The bug these cover: the plan named `home-exterior`, the resolver correctly
returned a cottage porch, and because a porch shows no mascot the clause told
the model NOT to take the mascot's appearance from it. Nothing else was
attached, so the model invented an animal. Category matching was never wrong
here -- it just cannot answer a question about the mascot.

Real files under a `tmp_path` brand dir; nothing about the resolver is mocked.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from lib.crew.reference_clauses import (
    grounding_clause,
    identity_clause,
    paired_reference_clause,
)
from lib.crew.reference_library import manifest_path, resolve_reference
from lib.crew.reference_mascot import mascot_anchor
from tests._reference_library_fakes import write_library as _write_library


def _mixed_library(brand_dir: Path, **mascot_photos: str) -> None:
    """A scene collection with no mascot in it, plus mascot photos elsewhere.

    `mascot_a="studio-mascot"` seeds `studio-mascot/mascot_a.png` tagged
    `shows_mascot`, beside the untagged `home-exterior/porch-bytes.png` that
    stands in for the cottage porch of the live failure.
    """
    _write_library(
        brand_dir,
        {"home-exterior": "porch-bytes", **{c: m for m, c in mascot_photos.items()}},
        mascot_categories=tuple(mascot_photos.values()),
    )


# ── mascot_anchor ────────────────────────────────────────────────────────────


def test_a_scene_photo_without_the_mascot_gets_one_anchored_beside_it(
    tmp_path: Path,
) -> None:
    """THE REGRESSION. A plan naming a non-mascot collection must still end up
    with a photo that actually shows the mascot attached to the generation."""
    _mixed_library(tmp_path, mascot_a="studio-mascot")
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None and not scene.shows_mascot

    anchor = mascot_anchor(tmp_path, scene)

    assert anchor is not None
    assert anchor.shows_mascot
    assert anchor.id == "studio-mascot/mascot_a.png"


def test_the_anchor_is_found_in_any_category_not_just_a_mascot_named_one(
    tmp_path: Path,
) -> None:
    """`shows_mascot` is the tagger's verdict; the tag is the operator's
    filing. A mascot photo filed under `forest-trail` is still a mascot photo,
    and restricting the search by name would reintroduce the miss."""
    _mixed_library(tmp_path, trail_shot="forest-trail")
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None

    anchor = mascot_anchor(tmp_path, scene)

    assert anchor is not None
    assert anchor.category == "forest-trail"


def test_a_scene_photo_that_already_shows_the_mascot_needs_no_second_photo(
    tmp_path: Path,
) -> None:
    """One picture doing both jobs is the good case -- a second would hand the
    model two versions of the same subject to reconcile."""
    _write_library(tmp_path, {"studio-mascot": "mascot-bytes"}, shows_mascot=True)
    scene = resolve_reference(tmp_path, "studio-mascot")
    assert scene is not None and scene.shows_mascot

    assert mascot_anchor(tmp_path, scene) is None


def test_a_library_with_no_mascot_photo_anchors_nothing(tmp_path: Path) -> None:
    """Nothing may substitute for a mascot photo the brand never uploaded --
    the same rule `resolve_reference` follows for a category it cannot match."""
    _write_library(tmp_path, {"home-exterior": "porch-bytes"})
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None

    assert mascot_anchor(tmp_path, scene) is None


def test_no_scene_photo_means_no_anchor(tmp_path: Path) -> None:
    """There is no generation to attach one to: the caller does not generate
    without a scene photo, and an anchor may never promote itself into that
    slot -- the planner's tag decides the scene."""
    _mixed_library(tmp_path, mascot_a="studio-mascot")

    assert mascot_anchor(tmp_path, None) is None


def test_the_seed_spreads_posts_across_the_mascot_photos_a_brand_keeps(
    tmp_path: Path,
) -> None:
    """Deterministic per seed (a re-run of one post reproduces its image) but
    not constant across seeds (two posts do not reuse one photo forever)."""
    _mixed_library(tmp_path, mascot_a="studio-mascot", mascot_b="forest-trail")
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None

    picks = {
        seed: mascot_anchor(tmp_path, scene, seed=seed) for seed in ("idea-1", "idea-2", "idea-3")
    }

    assert all(p is not None for p in picks.values())
    assert picks["idea-1"] == mascot_anchor(tmp_path, scene, seed="idea-1")
    assert len({p.id for p in picks.values() if p}) > 1


# ── paired_reference_clause ──────────────────────────────────────────────────


def test_the_paired_clause_labels_both_photos_by_position(tmp_path: Path) -> None:
    """The parts are sent scene-first, so the clause must call the scene PHOTO
    1 -- a swap here would tell the model to reproduce the porch as its
    mascot."""
    _mixed_library(tmp_path, mascot_a="studio-mascot")
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None
    anchor = mascot_anchor(tmp_path, scene)
    assert anchor is not None

    clause = paired_reference_clause(scene, anchor, "Nalla", "dog", "Nalla's Dad")

    assert clause.index("PHOTO 1") < clause.index("PHOTO 2")
    assert f"PHOTO 1 -- {grounding_clause()}" in clause
    assert f"PHOTO 2 -- {identity_clause('Nalla', 'dog')}" in clause
    assert "follow PHOTO 2 for that subject" in clause


def test_the_paired_clause_keeps_the_persona_identity_on_a_persona_scene(
    tmp_path: Path,
) -> None:
    """A `persona-portrait` scene photo must still get the persona identity
    clause -- adding the mascot anchor may not demote the person to scenery."""
    _mixed_library(tmp_path, mascot_a="studio-mascot")
    manifest = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    for entry in manifest["images"]:
        if entry["category"] == "home-exterior":
            entry["shows_persona"] = True
    manifest_path(tmp_path).write_text(json.dumps(manifest), encoding="utf-8")
    scene = resolve_reference(tmp_path, "home-exterior")
    assert scene is not None and scene.shows_persona
    anchor = mascot_anchor(tmp_path, scene)
    assert anchor is not None

    clause = paired_reference_clause(scene, anchor, "Nalla", "dog", "Nalla's Dad")

    assert "the persona is Nalla's Dad" in clause
    assert "the mascot is Nalla, the brand's dog" in clause
    assert grounding_clause() not in clause
