"""Mascot preference inside a reference category — `resolve_reference`.

The failure this covers shipped live on 2026-08-27: a blog hero for a post
about a 50 lb shepherd mix came back showing a small wire-haired terrier.
Nothing errored. The planner picked a real, stocked category, a real photo
resolved, image2image ran and grounded the SCENE correctly -- but that photo
did not show the mascot, so the model invented a dog. `shows_mascot` decides
which prompt clause is attached, and it was never part of choosing the photo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.crew.reference_library import library_root, manifest_path, resolve_reference


def _write_mixed_library(brand_dir: Path, entries: list[tuple[str, str, bool]]) -> None:
    """`[(category, marker, shows_mascot)]` -> a library with mixed categories.

    The shared fake writes one photo per category; the bug needs a category
    holding BOTH kinds, which is what a real collection looks like
    (`forest-trail` was 2 mascot photos out of 4).
    """
    root = library_root(brand_dir)
    images: list[dict[str, Any]] = []
    for category, marker, shows_mascot in entries:
        (root / category).mkdir(parents=True, exist_ok=True)
        filename = f"{marker}.png"
        (root / category / filename).write_bytes(marker.encode())
        images.append(
            {
                "id": f"{category}/{filename}",
                "category": category,
                "filename": filename,
                "content_type": "image/png",
                "source": "upload",
                "label": marker,
                "shows_mascot": shows_mascot,
            }
        )
    categories = sorted({c for c, _m, _s in entries})
    manifest_path(brand_dir).parent.mkdir(parents=True, exist_ok=True)
    manifest_path(brand_dir).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"slug": c, "label": c.title()} for c in categories],
                "images": images,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write_mixed_library(
        tmp_path,
        [
            ("forest-trail", "trail-scene", False),
            ("forest-trail", "trail-with-dog", True),
            ("home-exterior", "house-only", False),
            ("studio-mascot", "studio-dog", True),
        ],
    )
    return tmp_path


def test_mixed_category_prefers_the_mascot_photo(brand_dir: Path) -> None:
    """The live bug: a category holding both kinds handed back the one
    without the dog."""
    got = resolve_reference(brand_dir, "forest-trail", seed="x", prefer_mascot=True)
    assert got is not None
    assert got.shows_mascot is True
    assert got.label == "trail-with-dog"


def test_without_the_flag_behaviour_is_unchanged(brand_dir: Path) -> None:
    """Reels and social posts pick scene photos on purpose -- this must stay
    opt-in rather than becoming a global preference."""
    got = resolve_reference(brand_dir, "forest-trail", seed="x")
    assert got is not None
    assert got.label in {"trail-scene", "trail-with-dog"}


def test_category_without_a_mascot_photo_falls_back_to_one_that_has_her(
    brand_dir: Path,
) -> None:
    """The module refuses cross-category substitution everywhere else, and
    `prefer_mascot` is the deliberate exception.

    The bargain is different once a caller has said the subject IS the mascot:
    the alternative is not "no anchor" but "an anchor without her in it",
    which produces a confidently wrong dog -- a terrier fronting a post about
    a shepherd mix. A studio portrait in the wrong setting is the lesser
    error, and unlike an invented dog it is obvious enough to notice. This
    brand will not have a mascot photo in every collection, so this is the
    normal path, not a rare one.
    """
    got = resolve_reference(brand_dir, "home-exterior", seed="x", prefer_mascot=True)
    assert got is not None
    assert got.shows_mascot is True
    assert got.category == "studio-mascot"


def test_in_category_mascot_photo_beats_the_fallback(brand_dir: Path) -> None:
    """Substitution is the last resort: a category holding its own mascot
    photo keeps it, so the requested scene survives whenever it can."""
    got = resolve_reference(brand_dir, "forest-trail", seed="x", prefer_mascot=True)
    assert got is not None
    assert got.category == "forest-trail"
    assert got.shows_mascot is True


def test_unknown_category_falls_back_rather_than_generating_unanchored(
    brand_dir: Path,
) -> None:
    """A planner typo or a tag the library has never seen used to mean no
    anchor at all, which is the worst outcome: a fully invented dog."""
    got = resolve_reference(brand_dir, "no-such-tag", seed="x", prefer_mascot=True)
    assert got is not None
    assert got.shows_mascot is True


def test_a_library_with_no_mascot_photo_anywhere_still_returns_nothing(
    tmp_path: Path,
) -> None:
    """The fallback cannot invent what the library does not hold."""
    _write_mixed_library(tmp_path, [("home-exterior", "house-only", False)])
    assert resolve_reference(tmp_path, "kitchen", seed="x", prefer_mascot=True) is None


def test_all_mascot_category_is_unaffected(brand_dir: Path) -> None:
    got = resolve_reference(brand_dir, "studio-mascot", seed="x", prefer_mascot=True)
    assert got is not None
    assert got.shows_mascot is True


def test_seeded_pick_is_reproducible_under_the_preference(brand_dir: Path) -> None:
    first = resolve_reference(brand_dir, "forest-trail", seed="s1", prefer_mascot=True)
    again = resolve_reference(brand_dir, "forest-trail", seed="s1", prefer_mascot=True)
    assert first is not None and again is not None
    assert first.id == again.id
