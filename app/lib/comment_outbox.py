"""Transactional outbox for outbound comments -- claim the post, then post.

The engagers used to mark a post deduped only *after*
`lib/engagement/inline_comment.py:199` got `posted=True` back from the
adapter. That ordering trusts the adapter to distinguish "submitted" from
"failed", and it cannot: `lib/ig/comment_post.py:66-85` sleeps three seconds
after the submit click and returns True unconditionally, while
`lib/engagement/adapters/instagram.py:237-238` turns *any* exception into
`CommentResult.failed(...)` -- including one raised after the comment already
landed. A comment that landed but was reported failed stayed retryable, and
the next run commented on the same post a second time.

This module inverts the order. The row IS the claim: it is inserted *before*
the network call, and the `comment_claims` primary key -- not an inspection of
the result -- is what refuses the second attempt. That works across processes
and across containers, which `lib.runtime.singleton.SingletonLock` cannot do
(its lock dir is engine-relative, so the api and worker containers hold
private, mutually invisible locks).

Usage::

    from lib import comment_outbox

    if not comment_outbox.claim("instagram", post.post_id, content=text,
                                post_url=post.post_url):
        return  # someone already owns this post

    result = commenter.comment(post, text)
    if result.posted:
        comment_outbox.settle("instagram", post.post_id)
    # otherwise leave the claim `pending`: the reconciler decides whether the
    # comment landed, and only it may `release()`.

Why this lives in top-level `lib/` and not `lib/engagement/`: that package is
mypy `strict` **plus** `disallow_any_explicit` (`pyproject.toml:91-108`), and
`lib.db.fetch_all` returns `list[dict[str, Any]]` -- a module inside it could
not so much as name `Any` to type its own rows. `lib/dedup_pg.py` and
`lib/engagements_db/*` sit at base settings for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg

from lib.brand_context import current_brand_id
from lib.db import execute, fetch_all
from lib.observability import get_logger

Platform = Literal["facebook", "instagram"]
ClaimStatus = Literal["pending", "posted"]

log = get_logger(__name__)

_MAX_PENDING_LIMIT = 1000


@dataclass(frozen=True)
class PendingClaim:
    """A claim that was taken but never settled -- the reconciler's input.

    `content` and `target_name` are load-bearing, not audit decoration:
    resolving a stale claim means reopening `post_url` and searching the post
    DOM for that exact comment text.
    """

    brand: str
    platform: str
    post_id: str
    post_url: str
    target_name: str
    content: str
    worker_label: str
    claimed_at: datetime


def _brand(explicit: str | None) -> str:
    """Resolve the brand scope for a claim row.

    Resolved per call, never at import: a module-level default is bound once,
    before `BRAND_DIR` is necessarily set, and cannot be corrected afterwards.
    That mistake already cost `lib/dedup_pg.py` 878 rows filed under the
    engine's own name; the same shape of bug here would file claims under a
    brand nobody reads, which is indistinguishable from having no outbox.
    """
    return explicit or current_brand_id()


def claim(
    platform: Platform,
    post_id: str,
    *,
    post_url: str = "",
    target_name: str = "",
    content: str = "",
    worker_label: str = "",
    brand: str | None = None,
) -> bool:
    """Take exclusive ownership of commenting on this post. Insert-or-fail.

    Returns True when this caller now owns the post, False when a claim
    already exists (pending or posted) -- do not comment in that case.

    **Only `UniqueViolation` is swallowed. Every other exception propagates.**
    That is the fail-closed contract and it is deliberately stricter than
    `lib.dedup_pg.record_done`, which is defensive because a lost like/scan
    mark costs at most a re-visit. A lost claim costs a duplicate comment on a
    stranger's post, so a database that cannot answer must stop the run rather
    than wave it through.
    """
    scope = _brand(brand)
    try:
        execute(
            """
            INSERT INTO comment_claims
                (brand, platform, post_id, status, post_url,
                 target_name, content, worker_label)
            VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)
            """,
            (scope, platform, post_id, post_url, target_name, content, worker_label),
        )
    except psycopg.errors.UniqueViolation:
        log.info(
            "comment_claim_refused",
            brand=scope,
            platform=platform,
            post_id=post_id,
            reason="already_claimed",
        )
        return False
    log.info(
        "comment_claim_taken",
        brand=scope,
        platform=platform,
        post_id=post_id,
        worker_label=worker_label,
    )
    return True


def settle(platform: Platform, post_id: str, *, brand: str | None = None) -> bool:
    """Mark a claim as actually posted. Returns True when a row was updated.

    Idempotent: re-settling an already-settled claim succeeds and keeps the
    FIRST `settled_at`, so the timestamp records when the comment went out
    rather than when someone last re-ran the reconciler over it.
    """
    scope = _brand(brand)
    updated = execute(
        """
        UPDATE comment_claims
        SET status = 'posted', settled_at = COALESCE(settled_at, NOW())
        WHERE brand = %s AND platform = %s AND post_id = %s
        """,
        (scope, platform, post_id),
    )
    log.info(
        "comment_claim_settled",
        brand=scope,
        platform=platform,
        post_id=post_id,
        updated=updated,
    )
    return updated > 0


def release(platform: Platform, post_id: str, *, brand: str | None = None) -> bool:
    """Give a *pending* claim back, reopening the post. True when one was freed.

    The `AND status = 'pending'` is the safety rail, not an optimization: a
    `posted` row can never be deleted by this function, so release can never be
    the thing that makes us comment on a post twice. The worst a wrong release
    can do is give away a claim whose comment never landed -- which is exactly
    what release is for.
    """
    scope = _brand(brand)
    deleted = execute(
        """
        DELETE FROM comment_claims
        WHERE brand = %s AND platform = %s AND post_id = %s AND status = 'pending'
        """,
        (scope, platform, post_id),
    )
    log.info(
        "comment_claim_released",
        brand=scope,
        platform=platform,
        post_id=post_id,
        deleted=deleted,
    )
    return deleted > 0


def claimed_post_ids(platform: Platform, brand: str | None = None) -> set[str]:
    """Every post id this brand has claimed on this platform, pending or posted.

    The bulk counterpart to `claim` for the scan-time gate, which would
    otherwise issue one round-trip per candidate: a scan checks ~570 posts per
    run inside a live browser session, and the composite primary key's leading
    columns (brand, platform) already answer this as a single indexed read --
    same rationale as `lib.dedup_pg.completed_entity_ids`.
    """
    rows = fetch_all(
        "SELECT post_id FROM comment_claims WHERE brand = %s AND platform = %s",
        (_brand(brand), platform),
    )
    return {str(r["post_id"]) for r in rows}


def pending_older_than(
    minutes: int,
    *,
    limit: int = 100,
    brand: str | None = None,
) -> list[PendingClaim]:
    """Claims still `pending` after `minutes` -- the reconciler's work queue.

    A claim outlives its run only when the process died between the INSERT and
    the settle, or when the adapter reported a failure it could not actually
    verify. Oldest first, so a backlog drains in the order it accumulated.
    `make_interval(mins => %s)` keeps the window a bound parameter instead of
    interpolating it into an interval literal.
    """
    rows = fetch_all(
        """
        SELECT brand, platform, post_id, post_url, target_name,
               content, worker_label, claimed_at
        FROM comment_claims
        WHERE status = 'pending'
          AND brand = %s
          AND claimed_at < NOW() - make_interval(mins => %s)
        ORDER BY claimed_at
        LIMIT %s
        """,
        (_brand(brand), minutes, max(1, min(limit, _MAX_PENDING_LIMIT))),
    )
    return [
        PendingClaim(
            brand=str(r["brand"]),
            platform=str(r["platform"]),
            post_id=str(r["post_id"]),
            post_url=str(r["post_url"]),
            target_name=str(r["target_name"]),
            content=str(r["content"]),
            worker_label=str(r["worker_label"]),
            claimed_at=r["claimed_at"],
        )
        for r in rows
    ]
