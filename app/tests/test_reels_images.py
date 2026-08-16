"""Tests for `scripts.reels_images` — the OpenArt-or-hero-image decision.

The contract both ways, which is the whole point of the module:

  * **authorized → OpenArt is genuinely used.** A valid stored token must
    actually produce AI-generated beat images; the fallback must not
    short-circuit that.
  * **unauthorized / unconfigured / broken → hero image, run continues.**
    Never raises, never a partial set.

DB-free: `openart_enabled` / `stored_auth_state` / `generate_image` are all
stubbed on the module under test.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import reels_images

from lib.crew.reels.models import ReelBeat, ReelPlan
from lib.oauth.openart import OpenArtAuthRequiredError

_HERO = b"hero-image-bytes"


def _plan(beats: int = 5) -> ReelPlan:
    return ReelPlan(
        beats=[
            ReelBeat(headline=f"h{i}", subcopy=f"s{i}", image_prompt=f"prompt {i}")
            for i in range(beats)
        ],
        ig_caption="ig",
        fb_caption="fb",
    )


@pytest.fixture()
def authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Brand configured for OpenArt with a usable stored token."""
    monkeypatch.setattr(reels_images, "openart_enabled", lambda _d: True)
    monkeypatch.setattr(reels_images, "stored_auth_state", lambda: "ok")


def _resolve(plan: ReelPlan) -> reels_images.ResolvedImages:
    return reels_images.resolve_images(plan, _HERO, brand_dir=Path("/brand"), idea_id="idea-1")


# ── authorized: OpenArt must actually be used ─────────────────────────────────


def test_authorized_generates_images_via_openart(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    async def _generate(prompt: str, **kwargs: object) -> bytes:
        prompts.append(prompt)
        # `/brand` has no mascot asset, so the reference degrades to the hero
        # -- the pre-existing behavior, kept for brands without the asset.
        assert kwargs["reference_image"] == _HERO
        return f"ai-{prompt}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)
    plan = _plan()

    resolved = _resolve(plan)

    assert resolved.source == "openart"
    assert prompts == [b.image_prompt for b in plan.beats]  # one call per beat
    assert resolved.images == [f"ai-{p}".encode() for p in prompts]
    assert _HERO not in resolved.images  # NOT the fallback
    assert (resolved.ai_count, resolved.total) == (5, 5)


def test_openart_is_retried_per_idea_not_cached_off(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure for one idea must not disable OpenArt for the next: the
    'try once, fail, fall back forever' regression."""
    calls: list[str] = []
    ideas_seen: list[str] = []

    async def _generate(prompt: str, **_kw: object) -> bytes:
        calls.append(prompt)
        # Exhaust BOTH attempts for the first idea, so it genuinely falls back.
        if len(ideas_seen) == 1:
            raise RuntimeError("transient network blip")
        return b"ai-image"

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    ideas_seen.append("first")
    first = _resolve(_plan(beats=1))
    ideas_seen.append("second")
    second = _resolve(_plan(beats=1))

    assert first.source == "fallback"
    assert second.source == "openart"  # attempted again, succeeded
    assert len(calls) == 3  # 2 exhausted attempts, then 1 success


# ── the OpenArt reference is the brand mascot, not the post's hero ───────────
#
# Live-reported: generated reels didn't look like the brand's mascot. The hero
# image was being passed as OpenArt's image2image reference, and a hero is
# routinely a Pexels stock dog -- so every beat was grounded on a stranger's
# dog. Reference and fallback are two different pictures.

_MASCOT = b"mascot-reference-bytes"


@pytest.fixture()
def with_mascot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    asset = tmp_path / "persona_mascot_reference.png"
    asset.write_bytes(_MASCOT)
    monkeypatch.setattr(reels_images, "resolve_reference_image_path", lambda _d: asset)
    return asset


def test_generation_is_grounded_on_the_mascot_not_the_hero(
    authorized: None, with_mascot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    async def _generate(prompt: str, **kwargs: object) -> bytes:
        seen.append(kwargs["reference_image"])
        return f"ai-{prompt}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan(beats=3))

    assert seen == [_MASCOT] * 3  # every beat grounded on the mascot
    assert _HERO not in seen  # the hero is NOT the reference
    assert resolved.source == "openart"


def test_a_failed_beat_still_falls_back_to_the_hero_not_the_mascot(
    authorized: None, with_mascot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback is what the reader SEES, so it stays the post's own hero;
    the mascot photo is direction for the generator, not a slide."""

    async def _generate(prompt: str, **_kw: object) -> bytes:
        raise RuntimeError("beat failed")

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan(beats=2))

    assert resolved.images == [_HERO, _HERO]
    assert _MASCOT not in resolved.images
    assert resolved.source == "fallback"


def test_a_brand_without_the_asset_keeps_using_the_hero_as_reference(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brand-agnostic: most brands have no mascot asset, and for them nothing
    about this pipeline changes."""
    seen: list[object] = []

    async def _generate(prompt: str, **kwargs: object) -> bytes:
        seen.append(kwargs["reference_image"])
        return b"ai"

    monkeypatch.setattr(reels_images, "resolve_reference_image_path", lambda _d: None)
    monkeypatch.setattr(reels_images, "generate_image", _generate)

    _resolve(_plan(beats=2))

    assert seen == [_HERO, _HERO]


def test_an_unreadable_mascot_asset_degrades_instead_of_raising(
    authorized: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that resolves but can't be read must not abort the reel."""
    missing = tmp_path / "gone.png"  # resolver returns it; the file isn't there
    monkeypatch.setattr(reels_images, "resolve_reference_image_path", lambda _d: missing)
    seen: list[object] = []

    async def _generate(prompt: str, **kwargs: object) -> bytes:
        seen.append(kwargs["reference_image"])
        return b"ai"

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan(beats=1))

    assert seen == [_HERO]
    assert resolved.source == "openart"


# ── unavailable: hero fallback, never an exception ────────────────────────────


def test_auth_required_falls_back_and_does_not_raise(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoked token surfaces only at call time. The run must continue with
    hero images rather than aborting the batch."""

    async def _generate(_prompt: str, **_kw: object) -> bytes:
        raise OpenArtAuthRequiredError("authorize me")

    monkeypatch.setattr(reels_images, "generate_image", _generate)
    plan = _plan()

    resolved = _resolve(plan)

    assert resolved.source == "fallback"
    assert resolved.images == [_HERO] * 5


def test_not_configured_skips_openart_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OpenArt traffic at all when the brand hasn't enabled it."""
    monkeypatch.setattr(reels_images, "openart_enabled", lambda _d: False)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not touch OpenArt when unconfigured")

    monkeypatch.setattr(reels_images, "generate_image", _boom)
    monkeypatch.setattr(reels_images, "stored_auth_state", _boom)

    resolved = _resolve(_plan())

    assert resolved.source == "fallback"
    assert resolved.images == [_HERO] * 5


def test_unauthorized_skips_the_network_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reels_images, "openart_enabled", lambda _d: True)
    monkeypatch.setattr(reels_images, "stored_auth_state", lambda: "missing")

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not call OpenArt with no usable token")

    monkeypatch.setattr(reels_images, "generate_image", _boom)

    assert _resolve(_plan()).source == "fallback"


def test_failed_beat_falls_back_alone_keeping_the_others(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE regression this module exists for. A single beat failing used to
    discard every image already generated, so a user who paid and waited for
    4 OpenArt images got a reel of the hero image repeated 5 times. Only the
    failed beat may fall back; its siblings keep their real images."""
    calls: list[str] = []

    async def _generate(prompt: str, **_kw: object) -> bytes:
        calls.append(prompt)
        if prompt == "prompt 3":  # every attempt for this beat fails
            raise RuntimeError("The operation was aborted due to timeout")
        return f"ai-{prompt}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan())

    assert resolved.images[3] == _HERO  # only the failed beat
    for index in (0, 1, 2, 4):
        assert resolved.images[index] == f"ai-prompt {index}".encode()
    assert resolved.ai_count == 4
    assert resolved.source == "mixed"  # NOT "fallback"


def test_failed_beat_is_retried_before_falling_back(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed failure was a transient submit timeout, so one retry
    should rescue the beat rather than degrade the reel."""
    attempts: list[str] = []

    async def _generate(prompt: str, **_kw: object) -> bytes:
        attempts.append(prompt)
        if prompt == "prompt 0" and attempts.count("prompt 0") == 1:
            raise RuntimeError("aborted due to timeout")
        return f"ai-{prompt}".encode()

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan())

    assert attempts.count("prompt 0") == 2  # retried once
    assert resolved.images[0] == b"ai-prompt 0"  # rescued, no fallback
    assert resolved.ai_count == 5
    assert resolved.source == "openart"


def test_auth_loss_midway_keeps_images_already_generated(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoked token mid-run must not retroactively discard earlier beats,
    and must not keep calling for the remaining ones."""
    calls: list[str] = []

    async def _generate(prompt: str, **_kw: object) -> bytes:
        calls.append(prompt)
        if prompt in ("prompt 0", "prompt 1"):
            return f"ai-{prompt}".encode()
        raise OpenArtAuthRequiredError("authorize me")

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan())

    assert resolved.images[0] == b"ai-prompt 0"
    assert resolved.images[1] == b"ai-prompt 1"
    assert resolved.images[2:] == [_HERO] * 3
    assert resolved.ai_count == 2
    assert calls.count("prompt 3") == 0  # stopped calling after auth loss


def test_all_beats_failing_reports_pure_fallback(
    authorized: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _generate(_prompt: str, **_kw: object) -> bytes:
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(reels_images, "generate_image", _generate)

    resolved = _resolve(_plan())

    assert resolved.images == [_HERO] * 5
    assert resolved.ai_count == 0
    assert resolved.source == "fallback"
