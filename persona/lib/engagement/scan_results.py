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


@dataclass(frozen=True)
class LikeOutcome:
    """Result of the like step for one post."""

    attempted: bool = False
    succeeded: bool = False


@dataclass(frozen=True)
class CommentOutcome:
    """Per-post result of the inline comment step.

    `failed` means the comment was drafted and submitted but the adapter
    could not post it (e.g. `lib/ig/comment_post.py`'s selector chain
    missed). That is a routine, transient outcome — distinct from
    `declined` (the agent chose not to engage), which is terminal.
    """

    attempted: bool = False
    posted: bool = False
    declined: bool = False
    failed: bool = False


@dataclass(frozen=True)
class PostOutcome:
    """Per-post counters returned by `process_post`."""

    pre_filter_reason: str | None = None
    like_attempted: bool = False
    like_succeeded: bool = False
    candidate_score: float | None = None
    comment_attempted: bool = False
    comment_posted: bool = False
    comment_declined: bool = False
    comment_failed: bool = False

    @property
    def is_retryable(self) -> bool:
        """True when this visit should NOT be recorded as seen.

        Only a failed comment submission is retryable: the post was worth
        commenting on and we never managed to, so the next run must be
        allowed to open it again. Every other outcome — pre-filtered, low
        score, agent-declined, liked, commented — is terminal.
        """
        return self.comment_failed
