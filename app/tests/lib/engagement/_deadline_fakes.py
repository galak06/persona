"""Scripted clock + canned IG data for the scan-deadline tests.

Shared by ``test_scan_deadline.py`` (where the pass stops) and
``test_scan_truncation.py`` (what a stopped pass reports and stamps), which
are separate files only because of the 300-line cap. No production deps
beyond the fake adapter, no I/O, and nothing here sleeps: the whole point is
to reach the deadline without spending a real second.

The ig-engager fuse and the healthy-pass length below are measurements, not
guesses -- the 2026-09-05 19:03Z run took 1512s of its 1800s budget, and the
deadline has to sit outside that.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post
from lib.engagement.scan_deadline import RESERVE_SECONDS, ScanDeadline
from tests.lib.engagement._pipeline_fakes import make_post, make_src

HEALTHY_PASS_SECONDS = 1512.0
IG_FUSE_SECONDS = 1800.0


class ScriptedClock:
    """Monotonic-shaped clock that jumps forward after N reads.

    The pipeline reads the clock once per checkpoint, so "expire after the
    3rd read" is how a test says "stop at the 4th checkpoint" without any
    real time passing.
    """

    def __init__(self, *, expire_after: int, before: float = 0.0, after: float = 1e6) -> None:
        self._expire_after = expire_after
        self._before = before
        self._after = after
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self._before if self.reads <= self._expire_after else self._after


def make_deadline(clock: ScriptedClock, *, at: float = 1_000.0) -> ScanDeadline:
    """A deadline driven by `clock`, carrying the real ig-engager numbers."""
    return ScanDeadline(
        deadline_at=at,
        budget_seconds=IG_FUSE_SECONDS,
        reserve_seconds=RESERVE_SECONDS,
        now=clock,
    )


def three_source_adapter() -> FakeAdapter:
    """Three platform-agnostic sources of two comment-candidate posts each.

    Sized so a scripted clock can stop the pass at a source boundary (expire
    after 3 reads) or between two posts of one source (after 2).
    """
    sources = [make_src("s1"), make_src("s2"), make_src("s3")]
    posts = {
        s.id: [make_post(f"{s.id}-p1", "food?"), make_post(f"{s.id}-p2", "food?")] for s in sources
    }
    return FakeAdapter("instagram", list(sources), posts)


def ig_post(post_id: str, source: FakeSource) -> Post:
    """A high-scoring IG question-post: liked AND commented in one visit."""
    return Post(
        platform="instagram",
        post_id=post_id,
        post_url=f"https://www.instagram.com/p/{post_id}/",
        text="best ollie dog food kibble nutrition recipe for my dog?",
        author="@dogtrainer",
        source_id=source.id,
        source_name=source.id,
        source_url=source.url,
        platform_extra={"category": "food", "like_count": 300, "comment_count": 4, "weeks_old": 0},
    )


def two_hashtag_adapter() -> FakeAdapter:
    """Two hashtags, one qualifying post each -- enough to truncate between."""
    tags = [
        FakeSource(id=t, name=t, url=f"https://www.instagram.com/explore/tags/{t}/")
        for t in ("dogfood", "dognutrition")
    ]
    return FakeAdapter("instagram", list(tags), {t.id: [ig_post(f"{t.id}-p1", t)] for t in tags})
