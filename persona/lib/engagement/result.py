"""Result type for OutboundAdapter.like() calls."""

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
