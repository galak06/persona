"""ONE RULE, PINNED IN ONE PLACE: only an uploaded photo may anchor a
generated image.

Two silent anchors used to exist, and neither was ever chosen by a person:

  * the legacy `data/assets/persona_mascot_reference.*`, which
    `resolve_reference` fell through to whenever the library had no match, and
  * the WP post's HERO image, which the reels and social generators then
    uploaded as the image2image reference when the resolver returned `None`.
    A hero is routinely a Pexels stock photo, so "the brand's dog" in a
    generated frame was a stranger's.

Both are gone. `resolve_reference` stops at `None`, and `None` means DO NOT
GENERATE -- the beat or the hook image keeps the post's own hero as the
finished picture, at zero API cost. The legacy asset re-enters only through
`import_legacy`, i.e. the operator's "Import legacy reference" button.

Real files under a `tmp_path` brand dir; only the image providers are stubbed.
Sibling files cover the neighbouring contracts: `test_reference_library.py`
(resolution order), `test_reels_images.py` (the OpenArt fallback contract),
`test_socialpost_compose.py` (the hook image).
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from scripts import reels_images

from lib.crew.reels.models import ReelBeat, ReelPlan
from lib.crew.reference_library import resolve_reference
from lib.crew.reference_library_store import import_legacy
from lib.crew.socialpost import compose, hook_render
from lib.crew.socialpost.models import SocialPostPlan
from tests._reference_library_fakes import write_legacy_asset as _legacy
from tests._reference_library_fakes import write_library as _write_library

_HERO = b"\xff\xd8hero-jpeg-bytes"


# ── helpers ──────────────────────────────────────────────────────────────────


def _reel(categories: list[str]) -> ReelPlan:
    return ReelPlan(
        beats=[
            ReelBeat(
                headline=f"h{i}",
                subcopy=f"s{i}",
                image_prompt=f"prompt {i}",
                reference_category=category,
            )
            for i, category in enumerate(categories)
        ],
        ig_caption="ig",
        fb_caption="fb",
    )


def _social(reference_category: str) -> SocialPostPlan:
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


@pytest.fixture()
def openart_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """An authorized OpenArt that records every call it is asked to make."""
    monkeypatch.setattr(reels_images, "openart_enabled", lambda _d: True)
    monkeypatch.setattr(reels_images, "stored_auth_state", lambda: "ok")
    recorded: list[dict[str, Any]] = []

    async def _generate(prompt: str, **kwargs: Any) -> bytes:
        recorded.append({"prompt": prompt, **kwargs})
        return f"ai-{len(recorded)}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)
    return recorded


@pytest.fixture()
def gemini_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    class _Generated:
        bytes_ = b"gemini-image"
        provider = "gemini"

    def _generate(brief: str, **kwargs: Any) -> _Generated:
        recorded.append({"brief": brief, **kwargs})
        return _Generated()

    # Called from `hook_render`, the module compose shares with the retry path.
    monkeypatch.setattr(hook_render, "generate_wp_image", _generate)
    return recorded


# ── the resolver: the library is the only source ─────────────────────────────


def test_the_legacy_asset_is_no_longer_a_resolution_tier(tmp_path: Path) -> None:
    """A brand with ONLY `persona_mascot_reference.png` and an empty library
    resolves to `None` -- it used to resolve to that file."""
    _legacy(tmp_path)

    assert resolve_reference(tmp_path, "eating") is None
    assert resolve_reference(tmp_path, None) is None


def test_a_legacy_jpg_is_equally_invisible(tmp_path: Path) -> None:
    _legacy(tmp_path, ".jpg")

    assert resolve_reference(tmp_path, "eating") is None


def test_importing_the_legacy_asset_is_what_makes_it_usable(tmp_path: Path) -> None:
    """The operator's click is the whole difference between "sitting on disk"
    and "allowed to anchor an image" -- and the original file survives it."""
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()
    assert resolve_reference(tmp_path, "eating") is None

    entry = import_legacy(tmp_path)

    assert entry is not None
    resolved = resolve_reference(tmp_path, "eating")
    assert resolved is not None
    assert resolved.id == entry["id"]
    assert resolved.path.read_bytes() == before
    assert legacy.is_file() and legacy.read_bytes() == before


# ── reels: no library photo, no OpenArt call ─────────────────────────────────


def test_an_empty_library_generates_no_reel_beats_at_all(
    openart_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Authorized and configured, but nothing to ground on: the whole reel is
    hero images and OpenArt is never touched."""
    resolved = reels_images.resolve_images(
        _reel(["eating"] * 5), _HERO, brand_dir=tmp_path, idea_id="idea-1"
    )

    assert openart_calls == []
    assert resolved.images == [_HERO] * 5
    assert (resolved.ai_count, resolved.total) == (0, 5)
    assert resolved.source == "fallback"


def test_a_legacy_asset_does_not_rescue_an_empty_library(
    openart_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The exact regression: this brand used to get five OpenArt calls grounded
    on an asset nobody uploaded."""
    _legacy(tmp_path)

    resolved = reels_images.resolve_images(
        _reel(["eating"] * 3), _HERO, brand_dir=tmp_path, idea_id="idea-1"
    )

    assert openart_calls == []
    assert resolved.source == "fallback"


def test_matched_beats_generate_and_unmatched_beats_do_not(
    openart_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A mixed reel. `eating` is in the library and `general` is not, so the
    resolver answers the `eating` beats and -- having no `general` and no other
    tag to fall through to -- nothing for the rest.
    """
    _write_library(tmp_path, {"eating": "eat-bytes"})

    resolved = reels_images.resolve_images(
        _reel(["eating", "eating", "eating"]), _HERO, brand_dir=tmp_path, idea_id="idea-1"
    )

    assert len(openart_calls) == 3
    assert {call["references"][0].data for call in openart_calls} == {b"eat-bytes"}
    assert _HERO not in resolved.images
    assert resolved.source == "openart"


def test_an_unmatched_beat_keeps_its_hero_while_its_siblings_generate(
    openart_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per beat, not per run: one beat with no reference must not cost the
    others their generated images, and must not itself be generated."""
    _write_library(tmp_path, {"eating": "eat-bytes"})
    real_resolve = reels_images.resolve_reference

    def _resolve(brand_dir: Path, category: str | None, **kwargs: Any) -> Any:
        return None if category == "walking" else real_resolve(brand_dir, category, **kwargs)

    monkeypatch.setattr(reels_images, "resolve_reference", _resolve)

    resolved = reels_images.resolve_images(
        _reel(["eating", "walking", "eating"]), _HERO, brand_dir=tmp_path, idea_id="idea-1"
    )

    assert len(openart_calls) == 2  # NOT three
    assert resolved.images[1] == _HERO
    assert resolved.images[0] != _HERO and resolved.images[2] != _HERO
    assert (resolved.ai_count, resolved.total) == (2, 3)
    assert resolved.source == "mixed"


# ── social hook image: same rule ─────────────────────────────────────────────


def test_the_hook_image_is_not_generated_without_a_library_photo(
    gemini_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    image, source = compose.generate_hook_image(
        _social("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert gemini_calls == []
    assert (image, source) == (_HERO, "fallback")


def test_the_hook_image_is_generated_once_a_photo_exists(
    gemini_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The other half of the same rule: an upload is all it takes to turn
    generation back on, and the upload -- not the hero -- is the reference."""
    _write_library(tmp_path, {"eating": "eat-bytes"})

    image, source = compose.generate_hook_image(
        _social("eating"), mascot_name="Nalla", hero_bytes=_HERO, brand_dir=tmp_path
    )

    assert len(gemini_calls) == 1
    assert gemini_calls[0]["reference_image_bytes"] == b"eat-bytes"
    assert (image, source) == (b"gemini-image", "gemini")
