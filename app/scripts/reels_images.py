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
import sys
from pathlib import Path
from typing import NamedTuple

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.crew.reels.models import ReelPlan
from lib.crew.reels.openart_client import generate_image
from lib.crew.wp_image import resolve_reference_image_path
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


def _load_mascot_reference(brand_dir: Path) -> bytes | None:
    """The brand's real persona+mascot photo, for OpenArt's image2image
    grounding -- or None when the brand has no such asset.

    Reuses `lib.crew.wp_image.resolve_reference_image_path`, the same
    resolver the WP hero-image generator already uses, so both pipelines
    agree on where a brand's mascot reference lives
    (`$BRAND_DIR/data/assets/persona_mascot_reference.{png,jpg,jpeg}`).
    """
    path = resolve_reference_image_path(brand_dir)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        # Never fatal: a missing/unreadable reference degrades to the hero
        # image, exactly as a brand without the asset already does.
        logger.warning("reels_mascot_reference_unreadable", path=str(path), error=str(exc))
        return None


def _generate_one_beat(prompt: str, reference_bytes: bytes, *, index: int) -> bytes | None:
    """One beat's image, or None if it couldn't be generated.

    `reference_bytes` is OpenArt's image2image grounding, NOT the fallback
    image -- see `resolve_images` for why those are two different pictures.

    Retries once on an ordinary failure. `OpenArtAuthRequiredError`
    propagates: authorization is a run-wide condition, so retrying it per
    beat would just repeat the same failure five times.
    """
    for attempt in range(1, _ATTEMPTS_PER_BEAT + 1):
        try:
            return anyio.run(
                functools.partial(generate_image, prompt, reference_image=reference_bytes)
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

    Two different pictures, previously conflated into one:

      * the FALLBACK image, shown verbatim when a beat can't be generated --
        the post's own hero, which is correct: it is what the article shows.
      * the REFERENCE image, which grounds what OpenArt draws. This must be
        the brand's mascot. Passing the hero here (the prior behavior) told
        OpenArt to keep the reel "visually consistent" with whatever the
        post's featured image happened to be -- routinely a Pexels stock
        dog -- so every generated beat looked like a stranger's dog rather
        than the brand's own. Live-reported: reels not matching the mascot.

    Brands without the asset fall back to the hero as the reference, i.e.
    exactly the previous behavior, so this stays brand-agnostic.
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

    mascot_bytes = _load_mascot_reference(brand_dir)
    reference_bytes = mascot_bytes if mascot_bytes is not None else hero_bytes
    logger.info(
        "reels_reference_image_selected",
        idea_id=idea_id,
        source="brand_mascot" if mascot_bytes is not None else "wp_hero",
    )

    images: list[bytes] = []
    ai_count = 0
    authorization_lost = False
    for index, beat in enumerate(plan.beats):
        if authorization_lost:
            images.append(hero_bytes)
            continue
        try:
            generated = _generate_one_beat(beat.image_prompt, reference_bytes, index=index)
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
