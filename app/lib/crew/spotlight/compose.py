"""Compose ONE product spotlight: a claimed `composing` row -> two captions,
two affiliate links and one hook image, landed back at `queued` for review.

Shaped after `lib.crew.socialpost.retry` -- claim, render, commit -- because it
is the same transaction under a different name. The row here is claimed by the
API (`insert_composing` mints it as `'composing'`), so what arrives is already
exclusive; this module's job is to finish it or to fail it, never to leave it
in between. What a spotlight is composed FROM -- the post, the links, the
writer plan, the flags -- is `lib.crew.spotlight.inputs`.

**Every exit writes the row -- except a dry run, which writes none.** A
composition that simply returned would leave a `'composing'` row invisible to
the review page until the 20-minute stale sweep noticed it, and the operator --
who clicked a button that charged them a writer call and an image call -- would
watch a spinner for twenty minutes to be told "compose_timeout" rather than
"no_reference_photo". So every failure marks the row with a reason from the
frozen vocabulary (`result.SpotlightResult`), and an unexpected exception marks
it `unexpected_error` before re-raising, so the worker still records the run as
an error. A dry run is the one exception, and has to be: it is pointed at a
REAL row to read what the prompt produced, so marking one there would let a
transient WP 502 fail the operator's spotlight terminally -- the dispatched run
would then stop at `not_composing` and the spotlight would silently disappear.

**A product the post never linked is a FLAG, not a failure.** The owner may
spotlight any catalog product; the post is the grounding, not the gate. The
prompt is told not to claim the article reviews it, the excerpt falls back to
the post's opening, and `product_not_in_post` rides along on the review card.
Refusing would reduce the feature to repeating what the article already said.

**This module cannot publish.** It imports no publisher, no worker and no
release sweep, and its only writes are the two that move a row out of
`'composing'` (`set_pending_review`, `mark_failed`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import derivatives_db, ideas_db
from lib.affiliate_resolver import ProductEntry
from lib.crew.brand_identity import read_brand_identity
from lib.crew.products.pool import load_candidate_pool
from lib.crew.socialpost.compose import compose_image
from lib.crew.socialpost.hook_render import render_from_reference
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.retry import resolve_retry_reference
from lib.crew.spotlight import inputs
from lib.crew.spotlight.post_products import asins_in_html, post_slug
from lib.crew.spotlight.result import SpotlightResult
from lib.crew.writer.context import mascot_facts_summary
from lib.observability import get_logger

logger = get_logger(__name__)

#: The `source` a composed spotlight carries -- the vocabulary the regular
#: social post already writes, so one badge on the review page reads both.
GENERATED_SOURCE = "gemini"

#: The only status this may compose. `set_pending_review` and `mark_failed`
#: both guard on it, so a row swept mid-run refuses both writes, never races.
COMPOSING = "composing"


def _stamp() -> str:
    """A per-run token varying the reference seed: the resolver's pick is
    reproducible from its seed by design, so two spotlights composed from one
    post would otherwise anchor on the identical photo."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _compose(row: dict[str, Any], *, brand_dir: Path, dry_run: bool) -> SpotlightResult:
    """The claimed-row half: post, product, links, plan, image, commit."""
    idea = ideas_db.get_idea(str(row["idea_id"]))
    if idea is None:
        return SpotlightResult(False, "idea_not_found")
    post = inputs.fetch_post(idea)
    if isinstance(post, str):
        return SpotlightResult(False, post)
    product = load_candidate_pool(brand_dir).get(str(row.get("product_key") or ""))
    if product is None:
        # The catalog is a file the owner edits, so a key can disappear between
        # the click and the run. Composing from the row's stored display name
        # alone would drop the catalog note every claim is grounded in.
        return SpotlightResult(False, "product_not_in_catalog")

    in_post = (product.asin or "").strip().upper() in asins_in_html(inputs.post_html(post))
    # A `?p=<id>` permalink carries no slug segment, so the idea's topic is
    # what the campaign tag ends up named after.
    slug = post_slug(
        post, wp_url=str(idea.get("wp_url") or ""), fallback_title=str(idea.get("topic") or "")
    )
    urls = inputs.affiliate_urls(product, slug)
    if isinstance(urls, str):
        return SpotlightResult(False, urls)

    mascot_facts = mascot_facts_summary(brand_dir)
    plan = inputs.build_plan(
        post,
        product=product,
        brand_dir=brand_dir,
        mascot_facts=mascot_facts,
        photo_is_owner_pick=bool(str(row.get("reference_category") or "")),
        product_in_post=in_post,
    )
    if plan is None:
        return SpotlightResult(False, "plan_failed")
    if dry_run:
        # Everything past here costs an image call and writes the row.
        logger.info(
            "spotlight_dry_run_plan",
            derivative_id=str(row["id"]),
            comment_keyword=plan.comment_keyword,
            reference_category=plan.reference_category,
            product_in_post=in_post,
            fb_caption=plan.fb_caption[:400],
            ig_caption=plan.ig_caption[:400],
        )
        return SpotlightResult(True, "dry_run")
    return _render_and_commit(
        plan,
        row=row,
        brand_dir=brand_dir,
        product=product,
        urls=urls,
        mascot_facts=mascot_facts,
        product_in_post=in_post,
    )


def _render_and_commit(
    plan: SocialPostPlan,
    *,
    row: dict[str, Any],
    brand_dir: Path,
    product: ProductEntry,
    urls: tuple[str, str],
    mascot_facts: str,
    product_in_post: bool,
) -> SpotlightResult:
    """Resolve a photo, generate, overlay, then land the row at `queued`."""
    derivative_id = str(row["id"])
    override = str(row.get("reference_category") or "")
    if override:
        # The owner picked a collection from a library they can see; the
        # planner could see neither. Forcing it onto the plan keeps the image
        # brief and the resolved photo talking about one scene.
        plan = plan.model_copy(update={"reference_category": override})
    seed = f"{derivative_id}:{_stamp()}"
    reference = resolve_retry_reference(
        brand_dir, requested=override, planned=plan.reference_category, seed=seed
    )
    if reference is None:
        # No WP-hero fallback, deliberately: a spotlight is an advertisement in
        # the brand's own voice, and a stock photo under it is worse than none.
        # The fix is to upload a photo, and the reason says so.
        return SpotlightResult(False, "no_reference_photo")
    logger.info(
        "spotlight_reference_selected",
        derivative_id=derivative_id,
        image_id=reference.id,
        category=reference.category,
        requested_category=override or "(none)",
        shows_mascot=reference.shows_mascot,
    )

    identity = read_brand_identity(brand_dir)
    # Scanned BEFORE anything is generated or written. As an argument to the
    # write below, a scanner that raised would orphan the image this run had
    # just paid for and saved, because the unlink runs only on a REFUSED write.
    flags = inputs.review_flags(
        plan,
        product=product,
        mascot_name=identity.mascot_name,
        mascot_facts=mascot_facts,
        product_in_post=product_in_post,
    )
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
        return SpotlightResult(False, "generation_failed")
    relative = compose_image(
        plan,
        idea_id=str(row["idea_id"]),
        image_bytes=image_bytes,
        brand_dir=brand_dir,
        ig_handle=f"@{os.environ.get('IG_USERNAME', brand_dir.name)}",
        # One file per derivative, not per idea: a post may carry several
        # spotlights, and `idea_id` would have them overwrite each other.
        filename_stem=f"spotlight-{derivative_id}",
    )
    if relative is None:
        return SpotlightResult(False, "overlay_failed")

    if not derivatives_db.set_pending_review(
        derivative_id,
        fb_caption=plan.fb_caption,
        ig_caption=plan.ig_caption,
        comment_keyword=plan.comment_keyword,
        image_path=relative,
        image_alt=plan.image_alt_text,
        source=GENERATED_SOURCE,
        validation_flags=flags,
        fb_affiliate_url=urls[0],
        ig_affiliate_url=urls[1],
    ):
        # The row was failed or swept while this run generated, so the image it
        # just wrote belongs to nothing: it goes, rather than sitting in the
        # pending directory forever.
        (brand_dir / relative).unlink(missing_ok=True)
        return SpotlightResult(False, "write_refused")
    return SpotlightResult(True, "composed", image_path=relative, source=GENERATED_SOURCE)


def _mark(derivative_id: str, result: SpotlightResult, *, dry_run: bool) -> SpotlightResult:
    """Fail the row with this result's reason, then hand the result back.

    ONE funnel for every failure, the two guard cases included even though
    their write is a provable no-op (`not_found` has no row; `not_composing` is
    refused by `mark_failed`'s own guard, which is how a row already carrying
    `compose_timeout` keeps that reason). A reason added later cannot forget to
    write itself.

    It is also the one place the dry run's promise is kept: a preview run is
    aimed at a live row, so it reports the reason and marks nothing.
    """
    if dry_run:
        logger.warning(
            "spotlight_dry_run_would_fail", derivative_id=derivative_id, reason=result.reason
        )
        return result
    derivatives_db.mark_failed(derivative_id, error=result.reason)
    logger.warning("spotlight_compose_failed", derivative_id=derivative_id, reason=result.reason)
    return result


def compose_spotlight(
    derivative_id: str, *, brand_dir: Path, dry_run: bool = False
) -> SpotlightResult:
    """Compose one `composing` derivative. Never publishes, never schedules.

    `dry_run` stops after the writer crew: the plan is logged, no image is
    generated and the row is left `'composing'` untouched -- on EVERY path,
    failures included -- which is what makes it safe to point at a real row to
    read what the prompt produces.
    """
    row = derivatives_db.get(derivative_id)
    if row is None:
        return _mark(derivative_id, SpotlightResult(False, "not_found"), dry_run=dry_run)
    if str(row.get("status") or "") != COMPOSING:
        # Rejected, already composed, or failed by the stale sweep while this
        # run waited its turn behind another flow. Stopping costs nothing.
        return _mark(derivative_id, SpotlightResult(False, "not_composing"), dry_run=dry_run)
    logger.info(
        "spotlight_compose_started",
        derivative_id=derivative_id,
        idea_id=str(row.get("idea_id") or ""),
        product_key=str(row.get("product_key") or ""),
        dry_run=dry_run,
    )
    try:
        result = _compose(row, brand_dir=brand_dir, dry_run=dry_run)
    except Exception:
        # Re-raised either way, so the worker records the run as an error; the
        # ROW is only failed when this run owns it, which a dry run never does.
        if not dry_run:
            derivatives_db.mark_failed(derivative_id, error="unexpected_error")
        raise
    if not result.ok:
        return _mark(derivative_id, result, dry_run=dry_run)
    logger.info(
        "spotlight_compose_succeeded",
        derivative_id=derivative_id,
        reason=result.reason,
        image_path=result.image_path,
    )
    return result
