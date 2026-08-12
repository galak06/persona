"""Beat-image resolution for the reels pipeline.

One decision, isolated here: which image does each of a reel's five beats
get -- its own OpenArt-generated image, or the WP post's hero image?

**The fallback is per beat, never per run.** An earlier revision discarded
every generated image when any single beat failed, so one slow generation
turned four paid-for AI images into a reel of the same hero image repeated
five times -- the user paid and waited for nothing. Each beat now keeps
whatever it got: successful beats use their own image, and only the beats
that actually failed fall back to the hero image.

**OpenArt remains an optional enhancement.** Not configured, not authorized,
out of credits, erroring -- all of it degrades gracefully to hero images and
the reel still composes. Authorization is an opt-in upgrade, never a
prerequisite, so a run without it is a plain success.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import NamedTuple

import anyio

from lib.crew.reels.models import ReelPlan
from lib.crew.reels.openart_client import generate_image
from lib.oauth.openart import OpenArtAuthRequiredError, openart_enabled
from lib.oauth.openart_store import stored_auth_state
from lib.observability import get_logger

logger = get_logger(__name__)

# One retry per beat: the observed failure was a client-side submit timeout,
# which is transient -- retrying costs one call and saves the beat from a
# needless hero fallback. Bounded at 2 attempts so a genuine outage (or an
# exhausted quota) still degrades quickly instead of hammering the API.
_ATTEMPTS_PER_BEAT = 2


class ResolvedImages(NamedTuple):
    """The five images, plus an honest account of where they came from."""

    images: list[bytes]
    ai_count: int  # beats that got a real OpenArt image
    total: int

    @property
    def source(self) -> str:
        """Label recorded on the idea row (`content_ideas.reel_source`).

        `mixed` exists precisely so a partly-AI reel is not filed as a pure
        fallback -- the mislabelling that hid the discard bug.
        """
        if self.ai_count == 0:
            return "fallback"
        if self.ai_count == self.total:
            return "openart"
        return "mixed"


def _generate_one_beat(prompt: str, hero_bytes: bytes, *, index: int) -> bytes | None:
    """One beat's image, or None if it couldn't be generated.

    Retries once on an ordinary failure. `OpenArtAuthRequiredError`
    propagates: authorization is a run-wide condition, so retrying it per
    beat would just repeat the same failure five times.
    """
    for attempt in range(1, _ATTEMPTS_PER_BEAT + 1):
        try:
            return anyio.run(
                functools.partial(generate_image, prompt, reference_image=hero_bytes)
            )
        except OpenArtAuthRequiredError:
            raise
        except Exception as exc:
            logger.warning(
                "reels_openart_beat_failed",
                beat_index=index,
                attempt=attempt,
                attempts_allowed=_ATTEMPTS_PER_BEAT,
                error=str(exc),
            )
    return None


def resolve_images(
    plan: ReelPlan, hero_bytes: bytes, *, brand_dir: Path, idea_id: str
) -> ResolvedImages:
    """One image per beat, plus how many are genuinely AI-generated.

    Never raises for an OpenArt problem -- the hero image always yields a
    usable set, so the caller composes a reel either way.
    """
    total = len(plan.beats)
    all_hero = ResolvedImages([hero_bytes] * total, 0, total)

    if not openart_enabled(brand_dir):
        logger.info(
            "reels_openart_unavailable", idea_id=idea_id, reason="not_configured", beats=total
        )
        return all_hero

    # Cheap local check before any network round trip: with no stored token
    # that could work, every per-beat call would fail identically.
    if stored_auth_state() == "missing":
        logger.info(
            "reels_openart_unavailable", idea_id=idea_id, reason="not_authorized", beats=total
        )
        return all_hero

    images: list[bytes] = []
    ai_count = 0
    authorization_lost = False
    for index, beat in enumerate(plan.beats):
        if authorization_lost:
            images.append(hero_bytes)
            continue
        try:
            generated = _generate_one_beat(beat.image_prompt, hero_bytes, index=index)
        except OpenArtAuthRequiredError:
            # Run-wide: a stored token whose refresh is rejected only fails
            # at call time. Stop calling; remaining beats use the hero image.
            logger.info(
                "reels_openart_unavailable", idea_id=idea_id, reason="not_authorized", beats=total
            )
            authorization_lost = True
            generated = None
        if generated is None:
            images.append(hero_bytes)
        else:
            images.append(generated)
            ai_count += 1

    resolved = ResolvedImages(images, ai_count, total)
    logger.info(
        "reels_images_resolved",
        idea_id=idea_id,
        source=resolved.source,
        ai_images=ai_count,
        hero_images=total - ai_count,
    )
    return resolved
