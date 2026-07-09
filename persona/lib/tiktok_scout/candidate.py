"""Candidate dataclass for the TikTok follow-scout.

A TikTokCandidate represents a creator surfaced by `scout_hashtag` but not
yet followed. The same handle discovered through two different hashtags
collapses to one row at the state-write step (`state.save_candidates`
dedups by handle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class TikTokCandidate:
    """A TikTok creator the scout believes is worth following.

    Frozen + slots: candidates flow read-only through the pipeline
    (scout -> filter -> follow), and we may hold hundreds per run.

    Attributes:
        handle: TikTok username without the leading @. Lowercase.
        display_name: Public display name if visible at discovery, else handle.
        bio: Public bio text if visible at discovery, else empty string. Used
            by geo_filter.is_north_america_likely.
        follower_count: Approximate follower count scraped from the page. Zero
            if the scraper could not parse the value.
        source_hashtag: The hashtag page we discovered this candidate through.
        discovered_at: UTC ISO-8601 timestamp of when the scout found them.
        status: Pipeline status. One of: "pending" | "followed" | "skipped".
    """

    handle: str
    display_name: str
    bio: str
    follower_count: int
    source_hashtag: str
    discovered_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    status: str = "pending"

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict for state persistence."""
        return {
            "handle": self.handle,
            "display_name": self.display_name,
            "bio": self.bio,
            "follower_count": self.follower_count,
            "source_hashtag": self.source_hashtag,
            "discovered_at": self.discovered_at,
            "status": self.status,
        }
