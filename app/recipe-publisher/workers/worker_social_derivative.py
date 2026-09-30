"""Product-spotlight publisher: posts one half of an approved derivative.

The publish half of the derivative track (`lib.derivatives_db`), invoked once
per row per platform by `lib.social_release.release_due` -- never a sweep.
`--platform fb` posts the Facebook Page photo once the approved spotlight's
slot arrives and arms `ig_due_at` (the documented FB<->IG gap); `--platform
ig` posts the IG feed post once that gap has elapsed, via the WP media library
(Meta needs a public image URL for container creation), and only then deletes
the local image -- both halves are done with it.

What differs from `worker_wp_ideas_social_post.py`, and why. A derivative is
CLAIMED before anything goes out -- `claim_publish` flips
'scheduled' -> 'fb_publishing' in ONE conditional UPDATE, due-check included.
The regular worker reads-then-publishes, so two overlapping release sweeps can
both pass its status check and both post. Here exactly one process can ever
hold a row, which inverts the error handling (full reasoning in
`lib/derivatives_db/publish.py`): a publisher that RAISED, or returned without
an id, left nothing live, so the claim goes back and the next sweep retries. A
publisher that RETURNED whose result write then failed is the `wedged`
outcome -- the row is LEFT in '*_publishing', which nothing selects, so the
live post can never be auto-reposted, yet the rate-limiter count and the
`engagements_db` row are still written because the post IS live.

Why this file DUPLICATES ~45 lines of `worker_wp_ideas_social_post.py` (the
`sys.path` bootstrap, `_PostStub`, `_touch_timeline`, brand-path resolution)
instead of importing a shared core: that worker has NO tests and publishes for
real to the brand's Facebook Page and Instagram account. Refactoring it blind,
in the same change that introduces a second publisher, is the higher regression
risk -- a mistake there is a live post, not a red test. The extraction is a
later refactor-only PR, once BOTH workers are covered; this one already is
(`tests/test_worker_social_derivative.py`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo root for lib/, this tree's root for publishers.* -- same pattern as
# worker_wp_ideas_social_post.py.
_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _RECIPE_PUBLISHER_ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RECIPE_PUBLISHER_ROOT))

from publishers.facebook import publish_photo_post_to_facebook
from publishers.instagram import publish_to_instagram

from lib import derivatives_db, rate_limiter
from lib.derivatives_db.statuses import FORMAT_FEED_POST
from lib.engagements_db import record_publish

log = logging.getLogger(__name__)

# config.json instagram.min_gap_between_feed_posts_hours / the documented
# FB<->IG gap ("Publishing Coordination", app/CLAUDE.md). Same number as the
# regular social-post worker: both tracks share one IG account.
_IG_GAP_HOURS = 4.0

#: Outcomes the release sweep treats as "nothing went wrong" -> exit 0.
_OK_OUTCOMES = ("published", "skipped", "claim_lost", "dry_run")


def _info(event: str, **fields: Any) -> None:
    """One structured log line, same JSON-in-msg shape as every other worker."""
    log.info(json.dumps({"event": event, **fields}))


def _error(event: str, **fields: Any) -> None:
    log.error(json.dumps({"event": event, **fields}))


class _PublisherRefusedError(RuntimeError):
    """Publisher returned no id -- nothing is live, so the claim goes back."""


@dataclass(frozen=True)
class _Half:
    """All that differs between the halves; `kind` is also the limiter action."""

    expected_status: str
    due_column: str
    rl_platform: str
    kind: str
    timeline_key: str


_HALVES = {
    "fb": _Half("scheduled", "fb_due_at", "facebook", "page_post", "last_fb_page_post"),
    "ig": _Half("fb_published", "ig_due_at", "instagram", "feed_post", "last_ig_feed_post"),
}


def _brand_dir() -> Path:
    brand = os.environ.get("BRAND_DIR")
    return Path(brand) if brand else _ROOT


def _resolve_brand_path(relative_path: str) -> Path:
    """`image_path` is stored relative to `BRAND_DIR` -- resolved against *this
    process's own*: composer and publisher may run in different environments
    (worker vs API container) sharing one bind-mounted tree."""
    return _brand_dir() / relative_path


@dataclass
class _PostStub:
    """Duck-typed stand-in for `generators.recipe.Recipe`; `publish_to_instagram`
    touches only `.ig_caption` (container caption) and `.slug` -- same trick as
    the regular social-post worker's `_PostStub`."""

    slug: str
    ig_caption: str


def _touch_timeline(key: str) -> None:
    """Best-effort update of `$BRAND_DIR/state/publishing_timeline.json` -- the
    file every skill is documented to consult for cross-platform gaps."""
    try:
        path = _brand_dir() / "state" / "publishing_timeline.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = datetime.now(UTC).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        _error("timeline_update_failed", key=key, error=str(exc))


def _record_live_post(row: dict[str, Any], half: _Half, *, permalink: str, content: str) -> None:
    """Count a post that is ALREADY live: rate limiter, `/published`, timeline.

    Deliberately swallows everything: past this point the Graph API accepted the
    post, so an exception escaping here would reach `_do_one`'s handler and
    RELEASE the claim -- re-publishing a post that already exists. Bookkeeping
    that fails is a logged annoyance; a duplicate post is not."""
    # `ref` is the engagements PRIMARY KEY (`dedup_id(platform, kind, ref)`, an
    # upsert); `source_ref` is the stored column. This feature makes MANY
    # derivatives per idea, so keying on idea_id would guarantee a collision --
    # the second spotlight for one post+platform would upsert over the first.
    source_ref = f"derivative:{row['id']}"
    try:
        rate_limiter.record_action(half.rl_platform, half.kind)
        record_publish(
            platform=half.rl_platform,
            kind=half.kind,
            permalink=permalink,
            content=content,
            source_ref=source_ref,
            ref=source_ref,
        )
        _touch_timeline(half.timeline_key)
    except Exception as exc:
        _error("social_derivative_record_failed", derivative_id=str(row["id"]), error=str(exc))


def _upload_image_for_ig(row: dict[str, Any]) -> str:
    """IG container creation needs a PUBLIC image URL; the WP media library
    provides one -- the same `lib.crew.wp_media.upload_wp_media` path the regular
    worker uses, under a `social-derivative-` name so the two tracks' uploads
    stay tellable apart."""
    from lib.crew.wp_image import GeneratedImage
    from lib.crew.wp_media import upload_wp_media
    from lib.sessions.wp_client import wp_client

    image = GeneratedImage(
        url="",
        alt_text=str(row.get("image_alt") or ""),
        provider=str(row.get("source") or "gemini"),
        bytes_=_resolve_brand_path(str(row.get("image_path") or "")).read_bytes(),
        content_type="image/jpeg",
    )
    with wp_client() as client:
        _media_id, source_url = upload_wp_media(client, image, f"social-derivative-{row['id']}")
    return source_url


def _publish_fb(row: dict[str, Any], half: _Half) -> tuple[str, str]:
    """FB half. Returns `(outcome, permalink)` where outcome is 'published' or
    'wedged'; raises only while nothing is live yet."""
    did, caption = str(row["id"]), str(row.get("fb_caption") or "")
    result = publish_photo_post_to_facebook(
        image_path=_resolve_brand_path(str(row.get("image_path") or "")),
        message=caption,
        alt_text=str(row.get("image_alt") or ""),
    )
    if not result.post_id:
        raise _PublisherRefusedError("facebook returned no post id")
    permalink = result.permalink or ""
    written = derivatives_db.set_fb_result(did, url=permalink, ig_gap_hours=_IG_GAP_HOURS)
    _record_live_post(row, half, permalink=permalink, content=caption)
    return ("published" if written else "wedged"), permalink


def _publish_ig(row: dict[str, Any], half: _Half) -> tuple[str, str]:
    """IG half. Same contract as `_publish_fb`, plus the image cleanup."""
    did, caption = str(row["id"]), str(row.get("ig_caption") or "")
    stub = _PostStub(slug=f"spotlight-{did}", ig_caption=caption)
    result = publish_to_instagram(stub, image_url=_upload_image_for_ig(row))
    if not result.media_id:
        raise _PublisherRefusedError("instagram returned no media id")
    permalink = result.permalink or ""
    written = derivatives_db.set_ig_result(did, url=permalink)
    _record_live_post(row, half, permalink=permalink, content=caption)
    # Both platforms are done -- only now is the local file disposable; also on
    # a wedged write, since nothing ever re-publishes a '*_publishing' row.
    # Suppressed because ONCE A POST IS LIVE, NO LATER BOOKKEEPING FAILURE MAY
    # RELEASE THE CLAIM: this sits inside `_do_one`'s try, whose handler calls
    # `release_publish_claim` -- a read-only mount, or a path swapped for a
    # directory, would flip 'ig_publishing' -> 'fb_published' with `ig_due_at`
    # still past, and the next sweep would post the SAME IG post twice. (The
    # writes above cannot raise: `derivatives_db.publish` catches -> False.)
    with suppress(OSError):
        _resolve_brand_path(str(row.get("image_path") or "")).unlink(missing_ok=True)
    return ("published" if written else "wedged"), permalink


def _do_one(derivative_id: str, platform: str, *, dry_run: bool) -> str:
    row = derivatives_db.get(derivative_id)
    if row is None:
        _error("social_derivative_publish_no_row", derivative_id=derivative_id)
        return "error"

    half = _HALVES[platform]
    where = {"derivative_id": derivative_id, "platform": platform}

    # Only 'feed_post' has one caption pair and one image. 'carousel' is a
    # declared format with no publisher yet: refuse loudly, never post a slide.
    if str(row.get("format") or "") != FORMAT_FEED_POST:
        _error("social_derivative_publish_unsupported_format", **where, format=row.get("format"))
        return "unsupported_format"

    if row.get("status") != half.expected_status:
        _info("social_derivative_publish_skipped_wrong_status", **where, status=row.get("status"))
        return "skipped"

    # Second line of defence on timing (the claim re-checks it a third time on
    # the database clock). The sweep only hands over due rows, but this worker is
    # directly invocable, and an early spotlight is what the spread prevents.
    due_at = row.get(half.due_column)
    if due_at is not None and due_at > datetime.now(UTC):
        _info("social_derivative_publish_skipped_not_due", **where, due_at=str(due_at))
        return "skipped"

    if not rate_limiter.can_act(half.rl_platform, half.kind):
        _error("social_derivative_publish_rate_limited", **where)
        return "rate_limited"

    # Stops BEFORE the claim on purpose: a dry run must leave the row exactly as
    # it found it, still due for the next real sweep. Everything above is a pure
    # read, so this is the last point where that holds.
    if dry_run:
        _info("social_derivative_publish_dry_run", **where)
        return "dry_run"

    if not derivatives_db.claim_publish(derivative_id, platform):
        _info("social_derivative_publish_claim_lost", **where)
        return "claim_lost"

    try:
        outcome, url = _publish_fb(row, half) if platform == "fb" else _publish_ig(row, half)
    except Exception as exc:
        derivatives_db.release_publish_claim(derivative_id, platform)
        _error("social_derivative_publish_failed", **where, error=str(exc))
        return "error"

    if outcome == "wedged":
        _error("social_derivative_result_write_failed", **where, url=url)
        return "wedged"

    _info(f"social_derivative_published_{platform}", **where, url=url)
    return "published"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish a product spotlight (one platform)")
    p.add_argument("--derivative-id", required=True)
    p.add_argument("--platform", required=True, choices=("fb", "ig"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    outcome = _do_one(args.derivative_id, args.platform, dry_run=args.dry_run)
    _info("social_derivative_publish_summary", summary=outcome, **vars(args))
    return 0 if outcome in _OK_OUTCOMES else 1


if __name__ == "__main__":
    sys.exit(main())
