"""Sources for the IG follow-scout.

Reads the curated competitor list from `data/competitors.json` (via the
shared loader in `lib.group_discovery.competitor_signals`) and filters
to rows that (a) are active and (b) have a non-null `ig_handle`.

Provides `round_robin_sources` so a single scout run draws from many
competitors instead of draining one — Instagram's anti-scrape signals
appear faster when many requests target the same source page.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from lib.group_discovery.competitor_signals import load_competitors


@dataclass(frozen=True, slots=True)
class IGSource:
    """A competitor selected as a scout source.

    Attributes:
        handle: IG username, no leading @, lowercase.
        name: Human-readable name from competitors.json (for logs).
        niche: Free-form tag (gps, fresh_food, kibble, content, etc.).
            Lets the scout weight by niche-fit later if needed.
    """

    handle: str
    name: str
    niche: str


def ig_sources() -> list[IGSource]:
    """Return all active competitors with a non-null `ig_handle`.

    Preserves the order in `competitors.json` so the curator's intent
    (most-relevant-first) survives. Deduplicates on lowercase handle.
    """
    seen: set[str] = set()
    out: list[IGSource] = []
    for c in load_competitors():
        raw = c.get("ig_handle")
        if not isinstance(raw, str) or not raw.strip():
            continue
        handle = raw.strip().lstrip("@").lower()
        if handle in seen:
            continue
        seen.add(handle)
        out.append(
            IGSource(
                handle=handle,
                name=str(c.get("name", handle)),
                niche=str(c.get("niche", "")),
            )
        )
    return out


def round_robin_sources(
    sources: list[IGSource] | None = None,
    cycles: int = 1,
) -> Iterator[IGSource]:
    """Yield sources in round-robin order for `cycles` full passes.

    Args:
        sources: Pre-loaded source list. Defaults to `ig_sources()`.
        cycles: How many times to walk the list. 1 = one pass.

    Yields:
        Each `IGSource` `cycles` times, interleaved.

    Example:
        With sources [A, B, C] and cycles=2 yields A, B, C, A, B, C.
        The caller decides when to stop pulling — e.g., when the
        per-run candidate cap is hit.
    """
    pool = sources if sources is not None else ig_sources()
    for _ in range(cycles):
        yield from pool
