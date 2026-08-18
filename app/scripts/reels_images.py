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

**The reference is picked per beat, from the brand's tagged library.** Each
beat names the kind of scene it shows (`ReelBeat.reference_category`) and
`lib.crew.reference_library.resolve_reference` answers with the brand photo that
actually matches it, so a "walking" beat is grounded on a walking photo
rather than on whichever single asset the brand happened to keep. One
`ReferenceCache` is shared by the whole run, so N distinct photos across five
beats cost N uploads, not five.
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
from lib.crew.reference_library import identity_clause, resolve_reference
from lib.crew.reference_validate import EXTENSION_BY_CONTENT_TYPE, sniff_mime
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
    non-object document all yield "", and `identity_clause` then simply omits
    the name rather than the pipeline failing over a cosmetic field.
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
) -> ReferenceUpload | None:
    """The library photo grounding one beat, or None when the brand has none.

    `category` is handed to the resolver verbatim -- "" and an unrecognised
    label are its business, not this caller's: it falls through to `general`,
    then to any other tag, then to the legacy asset. Seeding on
    `"{idea_id}:{index}"` spreads a reel's beats across the photos a category
    holds while keeping a re-run identical.
    """
    reference = resolve_reference(brand_dir, category, seed=f"{idea_id}:{index}")
    if reference is None:
        return None
    try:
        data = reference.path.read_bytes()
    except OSError as exc:
        # Never fatal: an unreadable reference degrades to the hero image as
        # OpenArt's grounding, exactly as a brand with no library already does.
        logger.warning("reels_reference_unreadable", path=str(reference.path), error=str(exc))
        return None
    logger.info(
        "reels_reference_selected",
        idea_id=idea_id,
        beat_index=index,
        requested_category=category,
        category=reference.category,
        image_id=reference.id,
    )
    # The file's REAL content type travels with it. The previous single-
    # reference call left OpenArt's default `image/jpeg` in place, so every
    # PNG reference -- including the legacy `persona_mascot_reference.png` --
    # was uploaded mislabelled.
    return ReferenceUpload(data, reference.path.name, reference.content_type)


def _hero_reference(hero_bytes: bytes) -> ReferenceUpload:
    """The WP hero image as a reference, labelled with its REAL content type.

    Same mislabelling `_beat_reference` fixes, on the fallback path:
    `ReferenceUpload`'s defaults declare `reference.jpg` / `image/jpeg`, so a
    PNG hero -- which is what the generated heroes actually are -- was
    uploaded to OpenArt as a JPEG. The hero arrives as bare bytes with no
    filename to trust, so the type is sniffed from the bytes themselves.
    Unrecognised bytes keep the dataclass defaults, i.e. exactly the previous
    behaviour.
    """
    content_type = sniff_mime(hero_bytes)
    extension = EXTENSION_BY_CONTENT_TYPE.get(content_type or "")
    if content_type is None or extension is None:
        return ReferenceUpload(hero_bytes)
    return ReferenceUpload(hero_bytes, f"reference{extension}", content_type)


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

      * the FALLBACK image, shown verbatim when a beat can't be generated --
        the post's own hero, which is correct: it is what the article shows.
      * the REFERENCE image, which grounds what OpenArt draws. This must be
        the brand's mascot. Passing the hero here (the prior behavior) told
        OpenArt to keep the reel "visually consistent" with whatever the
        post's featured image happened to be -- routinely a Pexels stock
        dog -- so every generated beat looked like a stranger's dog rather
        than the brand's own. Live-reported: reels not matching the mascot.

    The reference is now resolved PER BEAT from the brand's tagged library
    (`ReelBeat.reference_category`), so beats showing different scenes get
    different photos of the same real dog. Brands with no library at all
    still fall back to the hero as the reference, i.e. exactly the previous
    behavior, so this stays brand-agnostic.
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

    # Every prompt carries the same subject-consistency instruction the WP
    # hero generator already proved out: a reference photo is attached on
    # every call, so the model must be told to reuse the person and dog in it.
    identity = identity_clause(_mascot_name(brand_dir))
    seed = _run_seed(idea_id)
    # ONE cache for the run: the five beats routinely agree on a photo, and
    # without this each beat would re-upload the same bytes.
    cache = ReferenceCache()
    hero_reference = _hero_reference(hero_bytes)

    images: list[bytes] = []
    ai_count = 0
    authorization_lost = False
    for index, beat in enumerate(plan.beats):
        if authorization_lost:
            images.append(hero_bytes)
            continue
        library = _beat_reference(brand_dir, beat.reference_category, idea_id=idea_id, index=index)
        try:
            generated = _generate_one_beat(
                f"{identity}{beat.image_prompt}",
                library if library is not None else hero_reference,
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
