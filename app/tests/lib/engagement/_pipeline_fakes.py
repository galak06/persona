"""In-test collaborator fakes + factories for ``run_outbound_scan`` tests.

Lives next to ``test_pipeline.py`` to keep that file under the 300-line
limit. No production deps, no I/O, no ``tmp_path``. All fakes match the
structural shapes the pipeline's internal ``Protocol`` classes require.

The two-stage queue fake is gone with the queue itself: both engagers run
single-pass (``inline_comment=True``), and ``inline_comment=False`` is now
only a like-only degrade — there is no ``queue_io`` collaborator anymore.
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post


class FakeDedup:
    """In-test dedup double matching the pipeline's call signatures."""

    def __init__(self, seen: set[str] | None = None) -> None:
        self.seen = seen or set()
        self.engaged: list[tuple[str, str, str, str | None]] = []

    def is_duplicate(self, platform: str, post_id: str) -> bool:
        return post_id in self.seen

    def mark_engaged(
        self,
        platform: str,
        post_id: str,
        action: str,
        group_or_hashtag: str = "",
        status: str = "engaged",
    ) -> None:
        self.engaged.append((platform, post_id, action, group_or_hashtag))


class FakeIterateOnceDedup(FakeDedup):
    """Dedup double that ALSO implements the optional `mark_seen` capability.

    Mirrors `lib.scan_dedup.ScanDedup` (what both engagers pass): a post
    marked seen is a duplicate on the next run, whatever the outcome of this
    one. Plain `FakeDedup` has no `mark_seen` — that models the bare
    `deduplication` module, so tests can prove the capability probe keeps
    working for collaborators without it.
    """

    def __init__(self, seen: set[str] | None = None) -> None:
        super().__init__(seen)
        self.seen_marked: list[tuple[str, str]] = []

    def mark_seen(self, platform: str, post_id: str) -> None:
        self.seen_marked.append((platform, post_id))
        self.seen.add(post_id)


class FakeRateTracker:
    def __init__(
        self,
        *,
        visits_left: int = 99,
        likes_left: int = 99,
        comments_left: int = 99,
    ) -> None:
        self.visits_left = visits_left
        self.likes_left = likes_left
        self.comments_left = comments_left
        self.recorded: list[tuple[str, str]] = []
        self.delays: list[tuple[str, str]] = []

    def can_act(self, platform: str, action: str) -> bool:
        if action == "group_visit":
            return self.visits_left > 0
        if action == "like":
            return self.likes_left > 0
        if action == "comment":
            return self.comments_left > 0
        return True

    def record_action(self, platform: str, action: str) -> int:
        self.recorded.append((platform, action))
        if action == "group_visit":
            self.visits_left -= 1
        if action == "like":
            self.likes_left -= 1
        if action == "comment":
            self.comments_left -= 1
        return 0

    def wait_random_delay(self, platform: str, action: str) -> None:
        self.delays.append((platform, action))


class FakeDrafter:
    """Drafter double. `engage=False` models the agent declining a post,
    which the real `draft_helper.SkillDrafter.draft_comment_for_post`
    signals by returning an empty string."""

    def __init__(self, *, engage: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self._engage = engage

    def draft_comment_for_post(
        self,
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
    ) -> str:
        self.calls.append({"platform": platform, "post_url": post_url})
        if not self._engage:
            return ""
        return f"DRAFT for {post_url}"


class FakeCommentGate:
    """`CommentGate` double: vetoes with a fixed reason, or allows with None.

    Records every `(post_id, source_url)` it was consulted for, so tests can
    assert both that the gate ran and that it ran per-post.
    """

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.checked: list[tuple[str, str]] = []

    def check(self, post: Post, source: object) -> str | None:
        self.checked.append((post.post_id, getattr(source, "url", "")))
        return self.reason


class FakeLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("info", msg))

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("warning", msg))


# --- Stub callables ---------------------------------------------------------


def stub_score(post: Post) -> float:
    """Deterministic scorer: 0.85 if 'food' in post.text, else 0.40."""
    return 0.85 if "food" in post.text else 0.40


def stub_now() -> str:
    return "2026-05-21T12:00:00+00:00"


# --- Factories --------------------------------------------------------------


def make_policy(
    *,
    fb_comment_quota: int = 5,
    ig_comment_quota: int = 10,
    fb_like_quota: int = 0,
) -> EngagementPolicy:
    return EngagementPolicy.from_config(
        {
            "content_analysis": {
                "relevance_threshold": 0.70,
                "approval_threshold": 0.80,
                "ig_comment_threshold": 0.75,
            },
            "rate_limits": {
                "facebook": {
                    "comments_per_day": fb_comment_quota,
                    "likes_per_day": fb_like_quota,
                },
                "instagram": {
                    "comments_per_day": ig_comment_quota,
                    "likes_per_day": 8,
                },
            },
        }
    )


def make_src(sid: str, name: str = "src") -> FakeSource:
    return FakeSource(id=sid, name=name, url=f"https://x/{sid}")


def make_post(
    pid: str,
    text: str,
    *,
    platform: str = "instagram",
    source_id: str = "s1",
    source_name: str = "src",
) -> Post:
    return Post(
        platform=platform,
        post_id=pid,
        post_url=f"https://x/p/{pid}",
        text=text,
        source_id=source_id,
        source_name=source_name,
        source_url="https://x/s",
    )


def make_ig_posts(n: int, *, has_question: bool = True) -> list[Post]:
    """Return ``n`` high-score IG posts (text contains 'food')."""
    suffix = "?" if has_question else "."
    return [make_post(f"p{i}", f"food question {i}{suffix}") for i in range(n)]


def run(
    adapter: FakeAdapter,
    *,
    policy: EngagementPolicy | None = None,
    dedup: FakeDedup | None = None,
    rate_tracker: FakeRateTracker | None = None,
    drafter: FakeDrafter | None = None,
    log: FakeLog | None = None,
    score: Callable[[Post], float] = stub_score,
    dry_run: bool = False,
    inline_comment: bool = False,
    comment_gate: FakeCommentGate | None = None,
) -> tuple[ScanReport, FakeDedup, FakeRateTracker, FakeDrafter]:
    """Run pipeline with sensible defaults; return report + collaborators.

    Pass `log=` a `FakeLog()` instance to inspect its `.calls` afterward --
    not part of the return tuple since most tests don't need it. Same for
    `comment_gate=` (the caller keeps its reference).
    """
    p = policy or make_policy()
    d = dedup or FakeDedup()
    rt = rate_tracker or FakeRateTracker()
    dr = drafter or FakeDrafter()
    lg = log or FakeLog()
    report = run_outbound_scan(
        adapter,
        p,
        dedup=d,
        rate_tracker=rt,
        drafter=dr,
        log=lg,
        now_iso=stub_now,
        score_relevance=score,
        dry_run=dry_run,
        inline_comment=inline_comment,
        comment_gate=comment_gate,
    )
    return report, d, rt, dr
