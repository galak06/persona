"""Tests for `scripts/comment_verify.py` — the outbox reconciler.

Unit tests: the outbox is a fake in-memory dict and the platforms are fake
page factories, because what is under test is the *adjudication*, not Postgres
(covered by `test_comment_outbox.py`) and not the DOM matcher (covered by the
own-comment tests). The one that matters most is
`test_inconclusive_lookup_leaves_the_claim_pending`: if a nav timeout ever
starts releasing claims, the transactional outbox stops preventing the
duplicate comment it was built to prevent.
"""
# ruff: noqa: S101

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from scripts import comment_verify

from lib.comment_outbox import PendingClaim
from lib.engagement.result import CommentLookup

_BRAND = "verify-brand"
_PAGE = object()  # stand-in for a Playwright Page; the fakes never touch it


def _claim(
    post_id: str,
    *,
    platform: str = "instagram",
    age: timedelta = timedelta(hours=1),
) -> PendingClaim:
    return PendingClaim(
        brand=_BRAND,
        platform=platform,
        post_id=post_id,
        post_url=f"https://example.test/{platform}/{post_id}/",
        target_name="rawdogfood",
        content="We tried this with Nalla last week — how long did your transition take?",
        worker_label="dogfood-ig-engager",
        claimed_at=datetime.now(UTC) - age,
    )


@dataclass
class _FakeOutbox:
    """The `comment_claims` table as a list plus two call logs."""

    pending: list[PendingClaim] = field(default_factory=list)
    settled: list[tuple[str, str]] = field(default_factory=list)
    released: list[tuple[str, str]] = field(default_factory=list)

    def pending_older_than(
        self, minutes: int, *, limit: int = 100, brand: str | None = None
    ) -> list[PendingClaim]:
        return list(self.pending)

    def settle(self, platform: str, post_id: str, *, brand: str | None = None) -> bool:
        self.settled.append((platform, post_id))
        return True

    def release(self, platform: str, post_id: str, *, brand: str | None = None) -> bool:
        self.released.append((platform, post_id))
        return True


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> _FakeOutbox:
    fake = _FakeOutbox()
    for name in ("pending_older_than", "settle", "release"):
        monkeypatch.setattr(comment_verify.comment_outbox, name, getattr(fake, name))
    return fake


@dataclass
class _FakePlatform:
    """A scripted platform: what each post URL looks like, and whether it opens."""

    lookups: dict[str, CommentLookup]
    session_fails: bool = False
    opened: int = 0
    visited: list[str] = field(default_factory=list)

    def factory(self) -> Any:
        @contextmanager
        def _open() -> Iterator[object]:
            self.opened += 1
            if self.session_fails:
                raise RuntimeError("SESSION_EXPIRED: Instagram login required")
            yield _PAGE

        return _open

    def find(self, page: object, post_url: str, text: str) -> CommentLookup:
        self.visited.append(post_url)
        return self.lookups[post_url]


def _install(monkeypatch: pytest.MonkeyPatch, **platforms: _FakePlatform) -> None:
    monkeypatch.setattr(
        comment_verify,
        "_PLATFORMS",
        {name: (p.factory(), p.find) for name, p in platforms.items()},
    )


@pytest.fixture
def no_side_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the three downstream writers unless a test captures them."""
    monkeypatch.setattr(comment_verify.engagements_db, "record_publish", lambda **_: "id")
    monkeypatch.setattr(comment_verify, "log_engagement", lambda *a, **k: None)
    monkeypatch.setattr(comment_verify.deduplication, "mark_engaged", lambda *a, **k: None)


def test_found_comment_settles_the_claim(
    outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ACK was lost but the comment is live — the claim becomes permanent."""
    claim = _claim("ABC")
    outbox.pending = [claim]
    ig = _FakePlatform({claim.post_url: CommentLookup.found(1)})
    _install(monkeypatch, instagram=ig)

    report = comment_verify.run_verify(platforms=("instagram",))

    assert outbox.settled == [("instagram", "ABC")]
    assert outbox.released == []
    assert (report.checked, report.settled, report.released) == (1, 1, 0)


def test_conclusively_absent_comment_releases_the_claim(
    outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """We read the comments and ours is not there — the post is retryable."""
    claim = _claim("DEF")
    outbox.pending = [claim]
    ig = _FakePlatform({claim.post_url: CommentLookup.absent()})
    _install(monkeypatch, instagram=ig)

    report = comment_verify.run_verify(platforms=("instagram",))

    assert outbox.released == [("instagram", "DEF")]
    assert outbox.settled == []
    assert (report.checked, report.released) == (1, 1)


@pytest.mark.parametrize("why", ["nav:TimeoutError", "login_wall", "post_unavailable"])
def test_inconclusive_lookup_leaves_the_claim_pending(
    why: str, outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Could-not-see is NOT did-not-post.

    A nav timeout or a login wall that released the claim would hand the post
    straight back to the next scan, which would comment on it a second time —
    the exact bug the outbox exists to prevent.
    """
    claim = _claim("GHI")
    outbox.pending = [claim]
    ig = _FakePlatform({claim.post_url: CommentLookup.inconclusive(why)})
    _install(monkeypatch, instagram=ig)

    report = comment_verify.run_verify(platforms=("instagram",))

    assert outbox.released == []
    assert outbox.settled == []
    assert (report.checked, report.inconclusive) == (1, 1)


def test_settled_claim_writes_the_engagements_row_the_failed_run_never_wrote(
    outbox: _FakeOutbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconciled comment must look identical downstream to a clean one."""
    published: list[dict[str, Any]] = []
    logged: list[tuple[Any, ...]] = []
    deduped: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        comment_verify.engagements_db,
        "record_publish",
        lambda **kw: published.append(kw) or "id",
    )
    monkeypatch.setattr(comment_verify, "log_engagement", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(comment_verify.deduplication, "mark_engaged", lambda *a: deduped.append(a))
    claim = _claim("JKL")
    outbox.pending = [claim]
    _install(monkeypatch, instagram=_FakePlatform({claim.post_url: CommentLookup.found(1)}))

    comment_verify.run_verify(platforms=("instagram",))

    assert len(published) == 1
    row = published[0]
    assert row["platform"] == "instagram"
    assert row["kind"] == "comment"
    assert row["status"] == "posted"
    assert row["ref"] == "JKL"
    assert row["content"] == claim.content
    # The comment went out when it was CLAIMED, not when we noticed.
    assert row["posted_at"] == claim.claimed_at.isoformat()
    assert logged == [("comment", "instagram", claim.target_name, claim.content)]
    assert deduped == [("instagram", "JKL", "comment", claim.target_name)]
    # The comment budget was spent on the day of the submission; the reconciler
    # must never charge it a second time.
    assert not hasattr(comment_verify, "rate_limiter")


def test_claim_older_than_max_age_days_is_settled_without_a_browser_visit(
    outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: a claim nobody can confirm becomes permanent, not a duplicate."""
    claim = _claim("OLD", age=timedelta(days=comment_verify.MAX_AGE_DAYS + 1))
    outbox.pending = [claim]
    ig = _FakePlatform({})  # a lookup would KeyError; nothing may reach it
    _install(monkeypatch, instagram=ig)

    report = comment_verify.run_verify(platforms=("instagram",))

    assert outbox.settled == [("instagram", "OLD")]
    assert ig.opened == 0
    assert ig.visited == []
    assert (report.aged_out, report.checked) == (1, 1)


def test_dry_run_changes_nothing(
    outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every branch still runs and is counted; no claim moves."""
    found = _claim("A1")
    absent = _claim("A2")
    aged = _claim("A3", age=timedelta(days=comment_verify.MAX_AGE_DAYS + 1))
    outbox.pending = [found, absent, aged]
    ig = _FakePlatform(
        {
            found.post_url: CommentLookup.found(1),
            absent.post_url: CommentLookup.absent(),
        }
    )
    _install(monkeypatch, instagram=ig)

    report = comment_verify.run_verify(platforms=("instagram",), dry_run=True)

    assert outbox.settled == []
    assert outbox.released == []
    assert (report.checked, report.settled, report.released, report.aged_out) == (3, 1, 1, 1)


def test_a_platform_with_an_expired_session_does_not_abort_the_other_platform(
    outbox: _FakeOutbox, no_side_writes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unopenable IG session says nothing about FB — or about IG's claims."""
    ig_claim = _claim("IG1", platform="instagram")
    fb_claim = _claim("FB1", platform="facebook")
    outbox.pending = [ig_claim, fb_claim]
    ig = _FakePlatform({ig_claim.post_url: CommentLookup.found(1)}, session_fails=True)
    fb = _FakePlatform({fb_claim.post_url: CommentLookup.found(1)})
    _install(monkeypatch, instagram=ig, facebook=fb)

    report = comment_verify.run_verify(platforms=("instagram", "facebook"))

    assert ig.visited == []
    assert outbox.settled == [("facebook", "FB1")]  # IG's claim stays pending
    assert (report.checked, report.settled) == (1, 1)


def test_cli_parses_platform_dry_run_and_limit() -> None:
    args = comment_verify._parse_args(["--platform", "facebook", "--dry-run", "--limit", "5"])
    assert args.platform == ["facebook"]
    assert args.dry_run is True
    assert args.limit == 5


def test_cli_tolerates_run_flow_flags() -> None:
    """`run_flow` owns `--health-check`; argparse must not choke on it."""
    args = comment_verify._parse_args(["--health-check"])
    assert args.platform is None
    assert args.limit == comment_verify.DEFAULT_LIMIT
