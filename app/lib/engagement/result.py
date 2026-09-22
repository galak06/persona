"""Result types for OutboundAdapter.like() / .comment() calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LikeResult:
    liked: bool
    reason: str  # "ok" | "skipped:<why>" | "failed:<why>"

    @classmethod
    def ok(cls) -> LikeResult:
        return cls(liked=True, reason="ok")

    @classmethod
    def skipped(cls, why: str) -> LikeResult:
        return cls(liked=False, reason=f"skipped:{why}")

    @classmethod
    def failed(cls, why: str) -> LikeResult:
        return cls(liked=False, reason=f"failed:{why}")


@dataclass(frozen=True)
class CommentResult:
    """Outcome of one inline comment submission. Mirrors `LikeResult`.

    Only adapters that implement `SupportsComment` (IG today) return these;
    the pipeline records a rate-limit action + dedup mark on `posted` only.
    """

    posted: bool
    reason: str  # "ok" | "skipped:<why>" | "failed:<why>"

    @classmethod
    def ok(cls) -> CommentResult:
        return cls(posted=True, reason="ok")

    @classmethod
    def skipped(cls, why: str) -> CommentResult:
        return cls(posted=False, reason=f"skipped:{why}")

    @classmethod
    def failed(cls, why: str) -> CommentResult:
        return cls(posted=False, reason=f"failed:{why}")


@dataclass(frozen=True)
class CommentLookup:
    """What LOOKING at a post told us about our own comment already being there.

    `CommentResult` answers "did this submission succeed?" — a question the
    submit path genuinely cannot answer, because an exception raised *after*
    the Post click is indistinguishable from one raised before it. This type
    answers the only question that can be answered honestly: "is our comment
    visible on the post right now?"

    Three states, not two. `inconclusive` is load-bearing, not a convenience
    wrapper around `absent`:

    - `absent`   — we loaded the post, expanded its comments, and our text is
                   not there. Safe to release the claim and comment.
    - `inconclusive` — we could not see the page at all (nav timeout, login
                   wall, deleted post, a DOM evaluate that blew up). We know
                   NOTHING. The claim must be left alone.

    Collapsing the second into the first is exactly how a verified-safe design
    turns back into the duplicate-comment bug: a nav timeout would read as
    "the comment never landed", the claim would be released, and the engager
    would comment a second time on a post it had already commented on. A
    caller that cannot handle three states must treat `inconclusive` as
    "already commented" (do nothing), never as "not commented".

    `count` is only meaningful when `conclusive` — it is 0 for `absent` and
    for every `inconclusive` value, where it means "unknown", not "none".
    """

    count: int
    reason: str  # "ok" | "inconclusive:<why>"

    @classmethod
    def found(cls, count: int) -> CommentLookup:
        """Our comment is on the post `count` times (callers pass count >= 1)."""
        return cls(count=count, reason="ok")

    @classmethod
    def absent(cls) -> CommentLookup:
        """We saw the post's comments and ours is not among them."""
        return cls(count=0, reason="ok")

    @classmethod
    def inconclusive(cls, why: str) -> CommentLookup:
        """We could not see the post. NOT the same as `absent` — see class doc."""
        return cls(count=0, reason=f"inconclusive:{why}")

    @property
    def conclusive(self) -> bool:
        """True when we actually read the post's comments."""
        return self.reason == "ok"

    @property
    def present(self) -> bool:
        """True only when we looked AND our comment was there."""
        return self.conclusive and self.count > 0
