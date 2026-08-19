"""What grounds the social-post hook image -- `lib.crew.socialpost.compose`.

The "stranger's dog" bug in its third home. `generate_hook_image` used to
condition Gemini on the WP post's own featured image, which is routinely a
Pexels stock dog, so the brand's mascot in every hook image was someone
else's. It now resolves the brand's real photo from the tagged library by the
plan's `reference_category`, and ONLY that: with no library match nothing is
generated and the hero ships as-is. The hero is the fallback OUTPUT, never a
reference.

Real files under a `tmp_path` brand dir; only `generate_wp_image` is stubbed.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.crew.reference_clauses import grounding_clause, identity_clause
from lib.crew.reference_library import library_root
from lib.crew.socialpost import compose
from lib.crew.socialpost.models import SocialPostPlan
from tests._reference_library_fakes import write_library as _write_library

_HERO = b"\xff\xd8hero-jpeg-bytes"  # real JPEG magic, so _sniff_mime agrees


def _plan(reference_category: str = "") -> SocialPostPlan:
    return SocialPostPlan(
        fb_caption="fb caption",
        ig_caption="ig caption",
        overlay_headline="headline",
        overlay_subcopy="subcopy",
        image_brief="a dog eating breakfast",
        reference_category=reference_category,
        cta_ribbon_text="FULL GUIDE",
        image_alt_text="alt",
        target_question="what should my dog eat?",
        comment_keyword="RECIPE",
    )


class _Generated:
    def __init__(self, data: bytes) -> None:
        self.bytes_ = data
        self.provider = "gemini"


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    def _generate(brief: str, **kwargs: Any) -> _Generated:
        recorded.append({"brief": brief, **kwargs})
        return _Generated(b"gemini-image")

    monkeypatch.setattr(compose, "generate_wp_image", _generate)
    return recorded


def test_the_library_photo_is_the_reference_not_the_hero(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    _write_library(tmp_path, {"eating": "eat-bytes", "general": "gen-bytes"})

    image, source = compose.generate_hook_image(
        _plan("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls[0]["reference_image_bytes"] == b"eat-bytes"
    assert calls[0]["reference_image_bytes"] != _HERO  # the stock dog is gone
    assert calls[0]["reference_image_mime"] == "image/png"  # real type, not sniffed
    assert (image, source) == (b"gemini-image", "gemini")


def test_an_empty_category_lands_on_general(calls: list[dict[str, Any]], tmp_path: Path) -> None:
    """Passed to the resolver verbatim; it -- not this caller -- picks general."""
    _write_library(tmp_path, {"eating": "eat-bytes", "general": "gen-bytes"})

    compose.generate_hook_image(
        _plan(""), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls[0]["reference_image_bytes"] == b"gen-bytes"


def test_an_empty_library_ships_the_hero_without_generating(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """ONLY UPLOADED PHOTOS MAY ANCHOR A GENERATED IMAGE. The hero used to be
    handed to Gemini as the reference here; now no library match means no
    generation call at all, and the post keeps its own hero."""
    image, source = compose.generate_hook_image(
        _plan("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls == []  # Gemini was never called
    assert (image, source) == (_HERO, "fallback")


def test_the_hero_remains_the_fallback_image_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reference changed; the fallback did NOT. What the reader sees when
    generation fails is still the post's own hero, never the mascot photo."""
    _write_library(tmp_path, {"general": "gen-bytes"})

    def _boom(_brief: str, **_kwargs: Any) -> _Generated:
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(compose, "generate_wp_image", _boom)

    image, source = compose.generate_hook_image(
        _plan("general"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert (image, source) == (_HERO, "fallback")


# ── the prompt clause follows what the photo actually shows ──────────────────


def test_a_mascot_photo_gets_the_identity_clause(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Only a photo tagged `shows_mascot` earns the 'reuse the EXACT person and
    dog' instruction, which is what keeps the brand's dog recognisable."""
    _write_library(tmp_path, {"eating": "eat-bytes"}, shows_mascot=True)

    compose.generate_hook_image(
        _plan("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls[0]["reference_clause"] == identity_clause("Nalla")


def test_a_non_mascot_photo_gets_the_grounding_clause(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The hook image's share of the bug: an untagged library photo is a dish or
    a kitchen, and demanding a dog that isn't in it corrupts the generation."""
    _write_library(tmp_path, {"eating": "eat-bytes"})

    compose.generate_hook_image(
        _plan("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls[0]["reference_clause"] == grounding_clause()
    assert "EXACT SAME" not in calls[0]["reference_clause"]
    assert "Nalla" not in calls[0]["reference_clause"]


def test_the_legacy_asset_alone_is_not_enough_to_generate(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """It used to resolve as a mascot reference straight off disk. Now only an
    operator's "Import legacy reference" click puts it in the library, so until
    then the hook image is the post's own hero."""
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    (assets / "persona_mascot_reference.png").write_bytes(b"legacy-bytes")

    image, source = compose.generate_hook_image(
        _plan("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls == []
    assert (image, source) == (_HERO, "fallback")


def test_an_unreadable_reference_ships_the_hero_rather_than_grounding_on_it(
    calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A manifest entry whose file was deleted between read and use must not
    take down the post -- and must not promote the hero into the reference
    slot either."""
    _write_library(tmp_path, {"general": "gen-bytes"})
    (library_root(tmp_path) / "general" / "gen-bytes.png").unlink()

    image, source = compose.generate_hook_image(
        _plan("general"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert calls == []
    assert (image, source) == (_HERO, "fallback")
