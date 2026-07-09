"""Candidate dataclass for the IG follow-scout.

A Candidate represents a user surfaced by `scout_followers` or
`scout_engagers` but not yet followed. The same user discovered through
two source signals collapses to one row at the state-write step
(`state.record_follow` dedups by handle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

SourceSignal = Literal["follower", "engager"]
"""How the candidate was discovered.

- "follower": scraped from a competitor's followers tab. Lower intent
  per user (passive following), larger pool.
- "engager": liked or commented on a competitor's recent post. Higher
  intent (active engagement), smaller pool.
"""


@dataclass(frozen=True, slots=True)
class Candidate:
    """A user the scout believes is worth following.

    Frozen + slots: candidates flow read-only through the pipeline
    (scout -> filter -> follow), and we'll hold thousands per run.

    Attributes:
        handle: IG username without the leading @. Lowercase.
        source_handle: The competitor handle (also no @) we discovered
            this candidate through. Used to round-robin sources later.
        source_signal: How we discovered them. See SourceSignal docstring.
        discovered_at: UTC ISO-8601 timestamp of when the scout found them.
        bio: Public bio text if visible at discovery, else None. Used
            by geo_filter.is_north_america_likely.
        display_name: Public display name if visible, else None.
        is_private: True if the account is private. Following a private
            account sends a request rather than a confirmed follow —
            success metrics differ.
        follower_count: Approximate follower count if scraped, else None.
            We skip accounts above the brand-threshold (~50k) elsewhere.
    """

    handle: str
    source_handle: str
    source_signal: SourceSignal
    discovered_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    bio: str | None = None
    display_name: str | None = None
    is_private: bool = False
    follower_count: int | None = None
