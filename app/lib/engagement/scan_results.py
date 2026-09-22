"""Result value objects for one `run_outbound_scan` invocation.

`ScanReport` is the public return type of the scan. `PostOutcome`,
`LikeOutcome` and `CommentOutcome` are the per-step results the internal
processing modules hand back to the orchestrator, which accumulates them
into the report.

Split out of `pipeline.py` (which was 645 lines, over the 300-line cap) so
the orchestrator, its collaborator protocols, and these results each live
in one module. Named `scan_results` rather than `results` to avoid
confusion with `lib/engagement/result.py`, which holds the *adapter*
action results (`LikeResult`, `CommentResult`).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScanReport:
    """Aggregated counters from one `run_outbound_scan` invocation.

    `pre_filtered` maps adapter rejection reason (e.g. "competitor",
    "own_account", "too_old") to count of posts dropped for that reason.
    `pre_filtered_posts` lists the (post_id, reason) pairs for those drops so
    callers can act on individual posts (e.g. permanently dedup-mark them).

    The counters below `pre_filtered_posts` exist to make the run's FUNNEL
    legible: every post `posts_scanned` counted left through exactly one exit,
    and until these fields existed most of those exits were unnamed. A scan
    that produced no candidates was indistinguishable from a scan whose post
    extraction had silently broken -- see `lib/engagement/extraction.py`.
    """

    platform: str
    sources_visited: int
    posts_scanned: int
    candidates: int
    likes_attempted: int
    likes_succeeded: int
    queued: int
    pre_filtered: dict[str, int] = field(default_factory=dict)
    # (post_id, reason) pairs for each pre-filtered drop.
    pre_filtered_posts: list[tuple[str, str]] = field(default_factory=list)
    # Inline-comment counters (single-pass mode; all 0 when inline_comment
    # is False). `comments_attempted` counts posts that cleared the quota
    # gate AND produced a draft, so a dry run still reports would-be work.
    comments_attempted: int = 0
    comments_posted: int = 0
    comments_declined: int = 0
    # Funnel exits. `duplicates` left at the dedup gate; `extraction_failed`
    # and `empty_caption` are adapter-reported scrape health (counted for
    # every enumerated post, whatever gate it later left through, so they
    # measure the SCRAPE and not the funnel); `scored_below_threshold` failed
    # `EngagementPolicy.is_candidate`.
    duplicates: int = 0
    extraction_failed: int = 0
    empty_caption: int = 0
    scored_below_threshold: int = 0
    # How much of the pass actually happened. `sources_total` is every source
    # the adapter offered THIS run -- for Instagram that is the hashtags due
    # today under `should_scan_today`, not the whole CSV -- and
    # `stopped_reason` names why the loop ended early (`"deadline"`,
    # `"rate_limit"`), `None` meaning it ran to the end. The pair exists
    # because a pass cut short reported exactly the numbers of a complete one:
    # `sources_visited` alone cannot say whether 12 was all there was.
    sources_total: int = 0
    stopped_reason: str | None = None


@dataclass(frozen=True)
class LikeOutcome:
    """Result of the like step for one post."""

    attempted: bool = False
    succeeded: bool = False


@dataclass(frozen=True)
class CommentOutcome:
    """Per-post result of the inline comment step.

    `failed` means the comment was drafted, claimed and SUBMITTED, and the
    adapter did not confirm it. It does not mean "did not post": an exception
    raised after the submit click is flattened into a failure, so the comment
    may well be live. The claim taken before the submit is what keeps that
    ambiguity safe.

    `blocked` means nothing was submitted at all — no claim could be taken
    (the store is down, the collaborator cannot claim, or someone else owns
    the post). Both are retryable; `declined` (the agent chose not to engage)
    is terminal.
    """

    attempted: bool = False
    posted: bool = False
    declined: bool = False
    failed: bool = False
    blocked: bool = False


@dataclass(frozen=True)
class PostOutcome:
    """Per-post counters returned by `process_post`."""

    pre_filter_reason: str | None = None
    # Which gate the post left through, when it left early. Both are pure
    # labels on branches that already existed -- nothing acts on them except
    # the report's funnel counters.
    duplicate: bool = False
    scored_below_threshold: bool = False
    like_attempted: bool = False
    like_succeeded: bool = False
    candidate_score: float | None = None
    comment_attempted: bool = False
    comment_posted: bool = False
    comment_declined: bool = False
    comment_failed: bool = False
    comment_blocked: bool = False

    @property
    def is_retryable(self) -> bool:
        """True when this visit should NOT be recorded as seen.

        Two outcomes qualify, for two different reasons:

        - `comment_blocked` — nothing was submitted at all: no claim could be
          taken (store down, collaborator can't claim, or the post is already
          owned). The post was worth commenting on and we never tried, so the
          next run must be allowed to open it again.
        - `comment_failed` — the comment WAS submitted and came back
          unconfirmed. Withholding the seen-mark is no longer what prevents a
          duplicate here: the `pending` claim taken before the submit already
          holds the post out of reach, and `lib/scan_dedup.py`'s
          `is_duplicate` consults it first. What withholding the mark buys is
          RELEASE — a `completed_tasks` seen-mark is permanent, so writing one
          would make `scripts/comment_verify.py`'s release a no-op and retire
          a post whose comment never actually landed.

        Every other outcome — pre-filtered, low score, agent-declined, liked,
        commented — is terminal.
        """
        return self.comment_failed or self.comment_blocked
