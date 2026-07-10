# pyright: reportMissingImports=false
"""Phase 7 of the recipe-publisher pipeline: rate limiting.

A pure, injectable per-platform daily-cap gate. Counts a platform's publishes
for a given day from a supplied history and compares against the platform's cap.
No global state — the caller passes the day and the history (built from
``recipes.publish_results``), which keeps it deterministic and testable.

Defaults are conservative and below each platform's real ceiling (see the FB
group-post cap memory). Tune via the ``caps`` argument.
"""

from __future__ import annotations

PHASE = "rate_limiting"

# Per-platform daily publish caps for the recipe pipeline.
DEFAULT_DAILY_CAPS: dict[str, int] = {"ig": 1, "fb": 1, "pinterest": 1}


class RateLimitGate:
    """Decides whether a platform may receive another publish on a given day."""

    def __init__(self, caps: dict[str, int] | None = None) -> None:
        self._caps = dict(caps) if caps is not None else dict(DEFAULT_DAILY_CAPS)

    def cap(self, platform: str) -> int:
        return self._caps.get(platform, 0)

    def used(
        self, platform: str, day: str, history: list[tuple[str, str]]
    ) -> int:
        """Count publishes to ``platform`` on ``day`` in ``history`` ((platform, day))."""
        return sum(1 for plat, when in history if plat == platform and when == day)

    def remaining(
        self, platform: str, day: str, history: list[tuple[str, str]]
    ) -> int:
        return max(0, self.cap(platform) - self.used(platform, day, history))

    def allow(
        self, platform: str, day: str, history: list[tuple[str, str]]
    ) -> bool:
        """True if ``platform`` is under its daily cap for ``day``."""
        return self.remaining(platform, day, history) > 0
