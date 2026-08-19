"""Beat-image resolution for the reels pipeline.

One decision, isolated here: which image does each of a reel's five beats
get -- its own OpenArt-generated image, or the WP post's hero image?

**The fallback is per beat, never per run.** An earlier revision discarded
every generated image when any single beat failed, so one slow generation
turned four paid-for AI images into a reel of the same hero repeated five
times. Each beat now keeps whatever it got: only the beats that actually
failed fall back to the hero image.

**OpenArt remains an optional enhancement.** Not configured, not authorized,
out of credits, erroring -- all of it degrades gracefully to hero images and
the reel still composes; a run without it is a plain success.

**The reference is picked per beat, from the brand's tagged library.** Each
beat names the kind of scene it shows (`ReelBeat.reference_category`) and
`lib.crew.reference_library.resolve_reference` answers with the photo that
matches. One `ReferenceCache` is shared by the run, so N distinct photos cost
N uploads, not five. **The prompt clause follows the photo**
(`lib.crew.reference_clauses`): mascot identity only for photos that show the
mascot, neutral scene-grounding for everything else.

**No library photo, no generation.** Only an UPLOADED photo may anchor a
generated image, so a beat the library can't answer for is never sent to
OpenArt -- it keeps the hero, exactly as a failed beat does. The hero used to
be uploaded as that beat's reference, telling OpenArt to match whatever the
post's featured image happened to be (routinely a Pexels stock dog). An
entirely unmatched reel costs nothing and reports `source="fallback"`.
"""

from __future__ import annotations

import functools
import hashlib
import json
import sys
from pathlib import Path
from typing import NamedTuple

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.crew.reels.models import ReelPlan
from lib.crew.reels.openart_client import ReferenceCache, ReferenceUpload, generate_image
from lib.crew.reference_clauses import reference_clause
from lib.crew.reference_library import ReferenceImage, resolve_reference
from lib.oauth.openart import OpenArtAuthRequiredError, openart_enabled
from lib.oauth.openart_store import stored_auth_state
from lib.observability import get_logger

logger = get_logger(__name__)

# One retry per beat: the observed failure was a client-side submit timeout,
# which is transient -- retrying costs one call and saves the beat from a
# needless hero fallback. Bounded at 2 attempts so a genuine outage (or an
# exhausted quota) still degrades quickly instead of hammering the API.
_ATTEMPTS_PER_BEAT = 2

#: Seeds are sent to an external image model; keep them inside a signed 32-bit
#: range, the widest value every provider we've seen accepts.
_SEED_MODULUS = 2**31


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


def _mascot_name(brand_dir: Path) -> str:
    """`site.mascot_name` from the brand config -- the same field
    `crewai_social_posts_pipeline._site_identity` and `crewai_content_pipeline`
    read, so every generator names the dog identically.

    Tolerant by design: no config, unreadable file, malformed JSON or a
    non-object document all yield "", and the identity clause then simply
    omits the name rather than the pipeline failing over a cosmetic field.
    """
    try:
        config = json.loads((brand_dir / "config.json").read_text(encoding="utf-8"))
        site = config.get("site", {}) if isinstance(config, dict) else {}
        return str(site.get("mascot_name") or "") if isinstance(site, dict) else ""
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("reels_mascot_name_unreadable", brand_dir=str(brand_dir), error=str(exc))
        return ""


def _run_seed(idea_id: str) -> int:
    """One seed for the whole reel, derived from the idea it belongs to.

    Five beats generated with one seed look like one shoot; five random
    seeds look like five unrelated pictures. Deriving it from `idea_id`
    (rather than randomizing) gives all three properties at once: consistent
    within a reel, different between reels, reproducible across re-runs.
    """
    return int(hashlib.sha256(idea_id.encode("utf-8")).hexdigest()[:8], 16) % _SEED_MODULUS


def _beat_reference(
    brand_dir: Path, category: str, *, idea_id: str, index: int
) -> tuple[ReferenceUpload, ReferenceImage] | None:
    """The library photo grounding one beat, or None when there is none.

    Returns the upload AND the entry, because the caller needs the entry's
    `shows_mascot` flag to pick this beat's prompt clause. `None` means this
    beat is NOT generated (see `resolve_images`), so both ways of getting
    there are logged -- distinguished by `reason`.

    `category` reaches the resolver verbatim: "" and an unrecognised label are
    its business, not this caller's. Seeding on `"{idea_id}:{index}"` spreads
    a reel's beats across the photos a category holds, re-runs identical.
    """
    reference = resolve_reference(brand_dir, category, seed=f"{idea_id}:{index}")
    which: dict[str, object] = {
        "idea_id": idea_id,
        "beat_index": index,
        "requested_category": category,
    }
    if reference is None:
        logger.info("reels_reference_unmatched", **which, reason="no_library_match")
        return None
    try:
        data = reference.path.read_bytes()
    except OSError as exc:
        # Never fatal, and never a substitute reference: the beat keeps the
        # hero image, exactly as an unmatched category does.
        logger.warning(
            "reels_reference_unmatched",
            **which,
            reason="unreadable",
            path=str(reference.path),
            error=str(exc),
        )
        return None
    logger.info(
        "reels_reference_selected",
        **which,
        category=reference.category,
        image_id=reference.id,
        # Which clause this beat will get, so a wrong-looking frame is
        # diagnosable from the log alone.
        shows_mascot=reference.shows_mascot,
    )
    # The file's REAL content type travels with it. The previous single-
    # reference call left OpenArt's default `image/jpeg` in place, so every
    # PNG reference -- including the legacy `persona_mascot_reference.png` --
    # was uploaded mislabelled.
    return ReferenceUpload(data, reference.path.name, reference.content_type), reference


def _generate_one_beat(
    prompt: str, reference: ReferenceUpload, *, index: int, seed: int, cache: ReferenceCache
) -> bytes | None:
    """One beat's image, or None if it couldn't be generated.

    `reference` is OpenArt's image2image grounding, NOT the fallback image --
    see `resolve_images` for why those are two different pictures. `cache` is
    the run's shared `ReferenceCache`, so a photo several beats agree on is
    uploaded once.

    Retries once on an ordinary failure. `OpenArtAuthRequiredError`
    propagates: authorization is a run-wide condition, so retrying it per
    beat would just repeat the same failure five times.
    """
    for attempt in range(1, _ATTEMPTS_PER_BEAT + 1):
        try:
            return anyio.run(
                functools.partial(
                    generate_image,
                    prompt,
                    references=[reference],
                    seed=seed,
                    reference_cache=cache,
                )
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

      * the FALLBACK image, shown verbatim when a beat isn't generated -- the
        post's own hero, which is correct: it is what the article shows.
      * the REFERENCE image, which grounds what OpenArt draws, resolved PER
        BEAT from the brand's tagged library (`ReelBeat.reference_category`).
        Passing the hero here (the prior behavior) told OpenArt to match the
        post's featured image -- routinely a Pexels stock dog.

    A beat the library can't answer for therefore takes the fallback WITHOUT
    a generation call: there is no second-choice anchor to reach for, so a
    brand with no library gets a reel of hero images at zero cost.
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

    # A library photo is attached on EVERY call, but only some show the
    # mascot: `reference_clause` sends the identity instruction for those and
    # the neutral grounding one for the rest.
    mascot_name = _mascot_name(brand_dir)
    seed = _run_seed(idea_id)
    # ONE cache for the run: the five beats routinely agree on a photo, and
    # without this each beat would re-upload the same bytes.
    cache = ReferenceCache()

    images: list[bytes] = []
    ai_count = 0
    authorization_lost = False
    for index, beat in enumerate(plan.beats):
        if authorization_lost:
            images.append(hero_bytes)
            continue
        library = _beat_reference(brand_dir, beat.reference_category, idea_id=idea_id, index=index)
        if library is None:
            # Nothing uploaded may anchor this beat, so it is never generated
            # (`_beat_reference` logged why). The hero counts as a hero image.
            images.append(hero_bytes)
            continue
        upload, resolved_reference = library
        try:
            generated = _generate_one_beat(
                f"{reference_clause(resolved_reference, mascot_name)}{beat.image_prompt}",
                upload,
                index=index,
                seed=seed,
                cache=cache,
            )
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
        # Distinct photos actually uploaded this run -- the cache's whole job.
        references_uploaded=len(cache),
    )
    return resolved
