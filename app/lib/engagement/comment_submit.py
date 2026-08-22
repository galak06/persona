"""Post one drafted comment: CLAIM it, SUBMIT it, then SETTLE the claim.

The "do" half of the inline comment step; `inline_comment.py` keeps the
"decide" half (the gate chain + the draft). Split out when the claim steps
pushed that module past the 300-line cap.

Three steps, and the ORDER is the whole point:

  1. **CLAIM** — `SupportsCommentClaim.claim_comment` writes a durable
     `pending` row *before* the network call. Nothing used to claim the post
     first: `inline_comment.py:199` called `commenter.comment(...)` and the
     dedup mark happened at `:246-276`, only *after* a reported success.
  2. **SUBMIT** — `commenter.comment(post, text)`.
  3. **SETTLE** — flip the claim to `posted`, then record the engagement.

Why a claim rather than a smarter check of the result: the result cannot be
trusted. `lib/ig/comment_post.py` clicks Post and (before this slice) returned
True unconditionally, and `lib/engagement/adapters/instagram.py:237-238` /
`adapters/facebook.py:338-339` flatten *any* exception — including one raised
after the comment already landed — into `CommentResult.failed(...)`. So
`posted=False` means UNCONFIRMED, never "did not post".

That is why a failed submission deliberately writes NOTHING to `engagements`.
The old `record_publish(status="failed", ref=post.post_id)` upserted onto the
same primary key as the `posted` row (`lib/engagements_db/repository.py:65-69`
is `INSERT ... ON CONFLICT (id) DO UPDATE` keyed on `dedup_id(platform, kind,
ref)`), so reporting the failure erased the evidence that an earlier attempt's
comment had landed. The `pending` claim is the durable record now, and
`scripts/comment_verify.py` adjudicates it by going and LOOKING at the post.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lib import engagements_db
from lib.engagement.adapter import Source, SupportsComment
from lib.engagement.collaborators import Dedup, Log, RateTracker, SupportsCommentClaim
from lib.engagement.log import log_engagement
from lib.engagement.post import Post
from lib.engagement.scan_results import CommentOutcome


def submit_comment(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    text: str,
    commenter: SupportsComment,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> CommentOutcome:
    """Claim the post, post the drafted comment, settle the claim, record it."""
    if not _claim(post=post, platform=platform, text=text, dedup=dedup, log=log):
        return CommentOutcome(blocked=True)

    result = commenter.comment(post, text)
    if not result.posted:
        # NOT "failed to post" — UNCONFIRMED. See the module docstring: no
        # `engagements` write happens here on purpose, because the row it
        # would write shares a primary key with the `posted` row and would
        # destroy it. The pending claim keeps the post blocked either way.
        log.warning(
            "post_comment_unconfirmed platform=%s post_id=%s reason=%s url=%s "
            "(claim left pending for scripts/comment_verify.py)",
            platform,
            post.post_id,
            result.reason,
            post.post_url,
        )
        return CommentOutcome(attempted=True, failed=True)

    _settle(post=post, platform=platform, dedup=dedup, log=log)
    _record_comment(
        post=post,
        source=source,
        platform=platform,
        score=score,
        text=text,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
    )
    return CommentOutcome(attempted=True, posted=True)


def _claim(*, post: Post, platform: str, text: str, dedup: Dedup, log: Log) -> bool:
    """Reserve this post before commenting. False means: do not comment.

    A collaborator without the capability is REFUSED, which is the deliberate
    inverse of `SupportsMarkSeen`'s no-op fallback. A missing `mark_seen`
    degrades open (worst case: a re-visit); a missing claim degrades closed,
    because the failure it would otherwise allow is a duplicate comment on a
    stranger's post — user-visible and only fixable by hand.
    """
    if not isinstance(dedup, SupportsCommentClaim):
        log.warning(
            "comment_claim_unsupported platform=%s post_id=%s url=%s "
            "(dedup collaborator cannot reserve a post; refusing to comment)",
            platform,
            post.post_id,
            post.post_url,
        )
        return False
    granted = dedup.claim_comment(
        platform,
        post.post_id,
        post_url=post.post_url,
        target_name=post.source_name or "",
        content=text,
    )
    if not granted:
        log.info(
            "comment_claim_refused platform=%s post_id=%s url=%s "
            "(already claimed, or the claim store is unavailable)",
            platform,
            post.post_id,
            post.post_url,
        )
    return granted


def _settle(*, post: Post, platform: str, dedup: Dedup, log: Log) -> None:
    """Flip the claim to `posted`. A settle failure is NOT a comment failure.

    The comment is already live and the claim is already `pending`, which keeps
    the post blocked — so nothing is at risk. Reporting a landed comment as
    failed is exactly the confusion this design removes, so the outcome stays
    `posted` and `scripts/comment_verify.py` picks the stale claim up later.
    """
    if not isinstance(dedup, SupportsCommentClaim):
        return
    if dedup.settle_comment(platform, post.post_id):
        return
    log.warning(
        "comment_claim_unsettled platform=%s post_id=%s url=%s "
        "(the comment IS live; claim stays pending for scripts/comment_verify.py)",
        platform,
        post.post_id,
        post.post_url,
    )


def _record_comment(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    text: str,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> None:
    """Spend the budget, mark engaged, persist (JSONL + DB), pace the next one."""
    rate_tracker.record_action(platform, "comment")
    dedup.mark_engaged(platform, post.post_id, "comment", source.name or "")
    log_engagement(
        "comment",
        platform,
        post.source_name or source.name or "",
        text,
        post_url=post.post_url,
        post_id=post.post_id,
        relevance_score=round(score, 3),
        post_text=post.text,
    )
    engagements_db.record_publish(
        platform=platform,
        kind="comment",
        status="posted",
        target_name=post.source_name or "",
        target_url=post.post_url,
        content=text,
        ref=post.post_id,
        posted_at=datetime.now(UTC).isoformat(),
    )
    log.info(
        "post_commented platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
    rate_tracker.wait_random_delay(platform, "comment")
