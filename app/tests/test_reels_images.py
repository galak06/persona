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
        assert kwargs["reference_image"] == _HERO  # mascot consistency
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
