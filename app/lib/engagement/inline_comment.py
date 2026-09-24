"""The inline (single-pass) comment step: DECIDE whether to comment.

Both engagers run this path via `run_outbound_scan(inline_comment=True)`:
`scripts/ig_engager.py` and `scripts/fb_engager.py` each like AND comment
a qualifying post in the single visit that opened it. The old Facebook
two-stage queue (scan -> queue file -> separate commenter run) is retired.

This module owns the gate chain and the draft; `comment_submit.py` owns the
claim -> submit -> settle sequence and the persistence that follows a posted
comment. They were one module until the claim steps pushed it past the
300-line cap.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from lib.engagement.adapter import Source, SupportsComment
from lib.engagement.collaborators import (
    CommentGate,
    Dedup,
    Drafter,
    Log,
    RateTracker,
    SupportsCommentClaim,
)
from lib.engagement.comment_submit import submit_comment
from lib.engagement.post import Post
from lib.engagement.scan_results import CommentOutcome


def maybe_comment(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    commenter: SupportsComment,
    drafter: Drafter,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    dry_run: bool,
    comment_gate: CommentGate | None = None,
) -> CommentOutcome:
    """Draft and post one comment during this post's visit.

    Order: comment gate -> comment quota -> claim outage -> draft ->
    claim/post/settle (`comment_submit.submit_comment`). The score floor is
    `policy.comment_threshold`, applied upstream in `post_processor`; there
    is no approval band above it — no human approves comments, so every
    candidate that reaches here is posted unless a later gate stops it.
    Under `dry_run` the drafter still runs (so the preview shows the real
    text) but nothing leaves the process: no claim, no `comment()`, no rate
    spend, no dedup mark.
    """
    if _blocked_by_comment_gate(post, source, platform, comment_gate, log):
        return CommentOutcome()
    if _blocked_by_comment_quota(platform, rate_tracker, log):
        return CommentOutcome()
    if _blocked_by_claim_outage(platform, dedup, log):
        return CommentOutcome(blocked=True)

    text = _draft(post, platform, drafter)
    if not text:
        _log_decline(post, platform, score, log, drafter)
        return CommentOutcome(declined=True)

    if dry_run:
        _log_dry_run(post, platform, score, text, log)
        return CommentOutcome(attempted=True)

    return submit_comment(
        post=post,
        source=source,
        platform=platform,
        score=score,
        text=text,
        commenter=commenter,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
    )


def _blocked_by_comment_gate(
    post: Post,
    source: Source,
    platform: str,
    comment_gate: CommentGate | None,
    log: Log,
) -> bool:
    """True when the injected `CommentGate` vetoes this post's comment.

    Runs first, before the quota/LLM steps (a vetoed post must spend nothing). A gated skip is terminal,
    not retryable: the post is still liked upstream and gets marked seen,
    so it is never revisited even after the gate later opens.
    """
    if comment_gate is None:
        return False
    reason = comment_gate.check(post, source)
    if reason is None:
        return False
    log.info(
        "comment_skipped_gated platform=%s post_id=%s reason=%s url=%s",
        platform,
        post.post_id,
        reason,
        post.post_url,
    )
    return True


def _blocked_by_comment_quota(platform: str, rate_tracker: RateTracker, log: Log) -> bool:
    """True if today's comment budget is spent (checked before any LLM call)."""
    if rate_tracker.can_act(platform, "comment"):
        return False
    log.info("rate_limit_exhausted platform=%s action=comment", platform)
    return True


def _blocked_by_claim_outage(platform: str, dedup: Dedup, log: Log) -> bool:
    """True when the claim store is down, so no comment can be reserved.

    Asked BEFORE `_draft` on purpose: `comment_submit._claim` would refuse
    every comment anyway, and a Postgres outage must not be paid for with an
    LLM call whose output is then thrown away. Collaborators without the
    capability fall through here and are refused later, at the claim itself,
    so the "cannot reserve" refusal is logged in exactly one place.
    """
    if not isinstance(dedup, SupportsCommentClaim):
        return False
    if dedup.claims_available():
        return False
    log.warning(
        "comment_skipped_claims_unavailable platform=%s (no draft attempted)",
        platform,
    )
    return True


def _draft(post: Post, platform: str, drafter: Drafter) -> str:
    """Ask the agentic drafter for comment text ("" means it declined)."""
    return drafter.draft_comment_for_post(
        platform=platform,
        post_text=post.text,
        group_or_hashtag=post.source_name,
        post_url=post.post_url,
    )


def _log_decline(
    post: Post, platform: str, score: float, log: Log, drafter: Drafter | None = None
) -> None:
    """Record why this post got no comment.

    The drafter is agentic and returns "" for several very different reasons:
    a genuine `engage:false` editorial decision, an upstream call that failed,
    a blank comment, or two voice-validation failures. This used to log all of
    them as `agent_declined_or_empty_draft`, so a total outage looked exactly
    like the agent being selective — which is how a retired model went
    unnoticed for a full run. Drafters that track it expose `last_outcome`;
    anything else (fakes, other implementations) falls back to the old label.
    """
    reason = getattr(drafter, "last_outcome", None) or "agent_declined_or_empty_draft"
    log.info(
        "comment_declined platform=%s post_id=%s score=%.2f reason=%s url=%s",
        platform,
        post.post_id,
        score,
        reason,
        post.post_url,
    )


def _log_dry_run(post: Post, platform: str, score: float, text: str, log: Log) -> None:
    """Show the comment a live run would have posted, without posting it."""
    log.info(
        "post_comment_dry_run platform=%s post_id=%s score=%.2f url=%s draft=%r (no comment sent)",
        platform,
        post.post_id,
        score,
        post.post_url,
        text,
    )
