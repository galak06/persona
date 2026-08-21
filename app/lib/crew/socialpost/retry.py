"""Regenerate the hook image of a post already sitting in the review queue.

A composed post whose image fell back to the WP hero (`source='fallback'`) is a
dead end on the review page: approving ships a stock photo under the brand's
name, rejecting throws away two captions that are usually fine. This module is
the third option -- keep the post, replace the image.

**Why a retry is not just "run the same generation again".** The live failure
that prompted this was::

    reference_library_no_match  available=forest-trail,home-exterior,... requested=(none)
    social_posts_reference_unmatched  reason=no_library_match  requested_category=

The planner emitted an EMPTY `reference_category`. `resolve_reference` answers
`None`, `generate_hook_image` skips generation entirely, and the hero ships.
Nothing about that is random: re-running it reproduces the identical fallback,
forever. A retry button wired to the same resolution would be a no-op wearing a
progress spinner.

So the retry resolves its reference differently, in three tiers, and only the
first of them is the planner's:

1. **The collection the operator picked**, when they picked one. They can see
   the post and the library; the planner could see neither.
2. **The fresh plan's own `reference_category`**, which is often perfectly good
   -- the failure mode is an empty or unmatched tag, not a wrong one.
3. **Any photo of the brand's mascot** (`reference_mascot.any_mascot_photo`),
   independent of every tag. This is the tier that makes the button honest: as
   long as the brand has uploaded one photo of its own mascot, a retry has
   something real to anchor on no matter what the planner says.

Tier 3 is deliberately absent from the composed path, where an unmatched tag
must stay a visible fallback rather than a silent substitution (see
`lib.crew.reference_library.resolve_reference`). The difference is consent: an
operator clicking Retry has asked for a different photo, and telling them "no"
is not caution, it is a broken button.

**The captions are not touched.** A fresh plan is drafted -- the image brief and
the overlay text are not stored anywhere, so there is nothing else to redraw
them from -- but only its image half is used. The row's existing FB and IG
captions and validation flags are written straight back unchanged.

**Nothing is destroyed on failure.** The new image is rendered to its own file;
the row is only re-pointed once that file exists, and the superseded image is
only unlinked once the row points at the new one. Every failure path restores
the post exactly as it was, still reviewable, still carrying its old image.

**This module cannot publish.** It imports no publisher and no release path,
and its only DB writes move a row between 'queued' and 'composing'.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import ideas_db, social_post_db, social_post_retry_db
from lib.crew import wp_source
from lib.crew.brand_identity import read_brand_identity, site_domain
from lib.crew.context import brand_voice_summary
from lib.crew.reference_library import ReferenceImage, list_category_labels, resolve_reference
from lib.crew.reference_mascot import any_mascot_photo
from lib.crew.socialpost import (
    build_social_post_agent,
    build_social_post_task,
    execute_social_post_crew,
)
from lib.crew.socialpost.compose import compose_image
from lib.crew.socialpost.hook_render import render_from_reference
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.prompts import build_social_post_task_description
from lib.observability import get_logger

logger = get_logger(__name__)

#: The `source` a post carries once a retry has actually generated its image.
#: Same vocabulary `generate_hook_image` writes, so the review page's badge and
#: its "this one needs a retry" test keep working off one set of values.
GENERATED_SOURCE = "gemini"


@dataclass(frozen=True)
class RetryResult:
    """What one retry did. `reason` is a stable machine-readable outcome, so
    the script's `summary:` line (and therefore `worker_runs.message`, and
    therefore the operator's error toast) says which of the several ways to
    fail actually happened."""

    ok: bool
    reason: str
    image_path: str = ""
    reference_id: str = ""
    source: str = ""


def _stamp() -> str:
    """A per-attempt token: varies the reference seed AND names the new file.

    Deliberately time-based rather than derived from the idea. A retry whose
    plan DID resolve a category must not pick the same photo out of it again --
    `resolve_reference`'s seeded pick is reproducible by design, so a seed of
    `idea_id` alone would hand back the identical photo and the operator would
    watch a paid-for run produce the picture they just rejected.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def resolve_retry_reference(
    brand_dir: Path, *, requested: str, planned: str, seed: str
) -> ReferenceImage | None:
    """The photo a retry anchors on: operator's pick, then the plan's, then any
    mascot photo. `None` only when the library holds nothing usable at all.

    The last tier is what separates this from `compose._reference`. Falling
    through to it is the NORMAL case for the failure this exists to fix -- an
    empty `reference_category` matches nothing at either of the first two.
    """
    for category in (requested, planned):
        if not category:
            continue
        reference = resolve_reference(brand_dir, category, seed=seed)
        if reference is not None:
            return reference
    anchor = any_mascot_photo(brand_dir, seed=seed)
    if anchor is None:
        logger.warning(
            "social_post_retry_no_reference",
            requested=requested or "(none)",
            planned=planned or "(none)",
        )
    return anchor


def _replan(idea: dict[str, Any], post: dict[str, Any], brand_dir: Path) -> SocialPostPlan | None:
    """A fresh `SocialPostPlan` for this post -- only its image half is used.

    The stored row carries the captions and the alt text, but not the image
    brief, the overlay headline/subcopy or the CTA ribbon, so there is no way
    to redraw the image from the database alone. One writer call is the price
    of the retry, and it is small beside the image call it precedes.
    """
    title = post.get("title", {}).get("rendered", "")
    body = wp_source.strip_html(post.get("content", {}).get("rendered", ""))[:3000]
    target_keyword = str(idea.get("target_keyword") or "")
    agent = build_social_post_agent()
    description = build_social_post_task_description(
        title=title,
        body=body,
        target_keyword=target_keyword,
        site_domain=site_domain(brand_dir),
        brand_voice=brand_voice_summary(brand_dir),
        reference_categories=list_category_labels(brand_dir, with_photos=True),
    )
    task = build_social_post_task(agent, description)
    return execute_social_post_crew(agent, task, target_keyword=target_keyword)


def _regenerate(
    idea: dict[str, Any], *, brand_dir: Path, reference_category: str, stamp: str
) -> RetryResult:
    """The claimed-row half: plan, resolve, generate, overlay, commit."""
    idea_id = str(idea["id"])
    wp_post_id = idea.get("wp_post_id")
    if not wp_post_id:
        return RetryResult(False, "no_wp_post")
    post = wp_source.fetch_post(str(wp_post_id))
    if post is None:
        return RetryResult(False, "wp_fetch_failed")

    plan = _replan(idea, post, brand_dir)
    if plan is None:
        return RetryResult(False, "plan_failed")

    seed = f"{idea_id}:{stamp}"
    reference = resolve_retry_reference(
        brand_dir, requested=reference_category, planned=plan.reference_category, seed=seed
    )
    if reference is None:
        return RetryResult(False, "no_reference_photo")
    logger.info(
        "social_post_retry_reference_selected",
        idea_id=idea_id,
        image_id=reference.id,
        category=reference.category,
        requested_category=reference_category or "(none)",
        planned_category=plan.reference_category or "(none)",
        shows_mascot=reference.shows_mascot,
        shows_persona=reference.shows_persona,
    )

    identity = read_brand_identity(brand_dir)
    image_bytes = render_from_reference(
        plan,
        reference,
        brand_dir=brand_dir,
        seed=seed,
        mascot_name=identity.mascot_name,
        mascot_kind=identity.mascot_kind,
        persona_name=identity.persona_name,
    )
    if image_bytes is None:
        return RetryResult(False, "generation_failed")

    # A NEW file, never the one being reviewed -- see `compose_image`.
    relative = compose_image(
        plan,
        idea_id=idea_id,
        image_bytes=image_bytes,
        brand_dir=brand_dir,
        ig_handle=f"@{os.environ.get('IG_USERNAME', brand_dir.name)}",
        filename_stem=f"{idea_id}-{stamp}",
    )
    if relative is None:
        return RetryResult(False, "overlay_failed")
    return _commit(idea, brand_dir=brand_dir, relative=relative, plan=plan, reference=reference)


def _commit(
    idea: dict[str, Any],
    *,
    brand_dir: Path,
    relative: str,
    plan: SocialPostPlan,
    reference: ReferenceImage,
) -> RetryResult:
    """Point the row at the new image, then retire the old file.

    The captions and validation flags go back in verbatim: `set_pending_review`
    writes the whole review payload, so passing anything else here would edit
    text the operator has already read. `image_alt` DOES change -- it describes
    the image, and the image is what was replaced.
    """
    idea_id = str(idea["id"])
    if not social_post_db.set_pending_review(
        idea_id,
        fb_caption=str(idea.get("social_post_fb_caption") or ""),
        ig_caption=str(idea.get("social_post_ig_caption") or ""),
        image_path=relative,
        image_alt=plan.image_alt_text,
        source=GENERATED_SOURCE,
        validation_flags=idea.get("social_post_validation_flags"),
    ):
        (brand_dir / relative).unlink(missing_ok=True)
        return RetryResult(False, "write_refused")

    previous = str(idea.get("social_post_image_path") or "")
    if previous and previous != relative:
        (brand_dir / previous).unlink(missing_ok=True)
    logger.info(
        "social_post_retry_succeeded",
        idea_id=idea_id,
        image_path=relative,
        image_id=reference.id,
        replaced=previous,
    )
    return RetryResult(
        True,
        "regenerated",
        image_path=relative,
        reference_id=reference.id,
        source=GENERATED_SOURCE,
    )


def retry_hook_image(idea_id: str, *, brand_dir: Path, reference_category: str = "") -> RetryResult:
    """Replace one queued post's hook image. Never publishes, never edits copy.

    The row is claimed `'queued' -> 'composing'` for the duration, which is
    what stops it being approved or rejected mid-flight (both of those are
    guarded on `'queued'`), and is restored to `'queued'` on every failure --
    including an unexpected exception, which is re-raised afterwards so the
    worker still records the run as an error.
    """
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        return RetryResult(False, "idea_not_found")
    if not social_post_retry_db.claim_for_recompose(idea_id):
        return RetryResult(False, "not_queued")

    stamp = _stamp()
    try:
        result = _regenerate(
            idea, brand_dir=brand_dir, reference_category=reference_category, stamp=stamp
        )
    except Exception:
        social_post_retry_db.restore_queued(idea_id)
        raise
    if not result.ok:
        social_post_retry_db.restore_queued(idea_id)
        logger.warning("social_post_retry_failed", idea_id=idea_id, reason=result.reason)
    return result
