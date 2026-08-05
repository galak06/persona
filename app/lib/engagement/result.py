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
