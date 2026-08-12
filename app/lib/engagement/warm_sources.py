"""Warmup-filtered source enumeration for Facebook groups (GAP-1).

Facebook flags accounts that join a group and engage immediately, so a
newly joined group gets a 48h comment warmup (`COMMENT_WARMUP_HOURS`)
before the engager may open its posts. This wrapper replaces the private
`_WarmFiltered` class that lived in the retired `scripts/fb_scan.py` —
same semantics, now shared library code for `scripts/fb_engager.py`.
"""

from __future__ import annotations

from typing import Any

from lib.engagement.adapter import OutboundAdapter, Source
from lib.group_warmup import COMMENT_WARMUP_HOURS, is_group_warm


class WarmFilteredAdapter:
    """Filter the FB adapter's sources by the comment-warmup gate.

    Delegates every other adapter method to `inner` so the pipeline still
    sees a normal OutboundAdapter. Keeps warmup at the engager edge —
    adapter stays pure platform-IO; pipeline stays platform-agnostic.
    """

    def __init__(self, inner: OutboundAdapter, *, hours: int = COMMENT_WARMUP_HOURS) -> None:
        self._inner = inner
        self._hours = hours
        self.platform: str = inner.platform

    def list_sources(self) -> list[Source]:
        return [s for s in self._inner.list_sources() if is_group_warm(s.url, self._hours)]

    # `Any` (not `object`) so mypy keeps treating the wrapper as a full
    # OutboundAdapter at call sites — delegated members must not degrade
    # to `object`. Same tradeoff the original fb_scan wrapper made.
    def __getattr__(self, name: str) -> Any:  # type: ignore[explicit-any]
        return getattr(self._inner, name)
