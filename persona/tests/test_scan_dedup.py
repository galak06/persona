"""Tests for ``lib.scan_dedup.ScanDedup``.

The class reconciles two dedup stores (the JSON cache and Postgres
``completed_tasks``). These tests pin the part that is easy to regress
silently: the Postgres side is read ONCE per platform into a set, not once
per post. A scan checks ~570 posts inside a live browser session, so a
per-post SELECT is ~570 sequential round-trips.

No Postgres and no JSON cache: both collaborators are monkeypatched at the
``lib.scan_dedup`` module boundary.
"""

from __future__ import annotations

from typing import Any

import pytest

from lib import scan_dedup
from lib.scan_dedup import ScanDedup


class _FakePg:
    """Counts calls so tests can assert the number of round-trips."""

    def __init__(self, seen: set[str] | None = None) -> None:
        self.seen = seen or set()
        self.fetch_calls: list[tuple[str, str]] = []
        self.write_calls: list[tuple[str, str, str]] = []
        self.fetch_should_fail = False
        self.write_should_fail = False

    def completed_entity_ids(self, task_type: str, platform: str) -> set[str]:
        self.fetch_calls.append((task_type, platform))
        if self.fetch_should_fail:
            raise RuntimeError("pg down")
        return set(self.seen)

    def record_done(
        self, task_type: str, platform: str, entity_id: str, **kwargs: Any
    ) -> bool:
        self.write_calls.append((task_type, platform, entity_id))
        if self.write_should_fail:
            raise RuntimeError("pg down")
        self.seen.add(entity_id)
        return True


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> _FakePg:
    """Redirect ScanDedup's Postgres calls at a counting fake."""
    fake = _FakePg()
    monkeypatch.setattr(scan_dedup, "completed_entity_ids", fake.completed_entity_ids)
    monkeypatch.setattr(scan_dedup, "record_done", fake.record_done)
    return fake


@pytest.fixture(autouse=True)
def _no_json_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the JSON dedup store answer "never seen", with no file I/O."""
    monkeypatch.setattr(
        scan_dedup.deduplication, "is_duplicate", lambda platform, post_id: False
    )
    monkeypatch.setattr(
        scan_dedup.deduplication, "mark_engaged", lambda *a, **k: None
    )


# --- the prefetch ------------------------------------------------------------


def test_postgres_is_read_once_not_once_per_post(pg: _FakePg) -> None:
    """The N+1 this class exists to avoid: one SELECT for the whole run."""
    dedup = ScanDedup("test-worker")
    for i in range(50):
        dedup.is_duplicate("instagram", f"p{i}")

    assert pg.fetch_calls == [("scan", "instagram")], "one bulk read, not 50"


def test_prefetched_ids_are_reported_as_duplicates(pg: _FakePg) -> None:
    """The prefetched set is actually consulted, not just fetched."""
    pg.seen = {"already_open"}
    dedup = ScanDedup("test-worker")

    assert dedup.is_duplicate("instagram", "already_open") is True
    assert dedup.is_duplicate("instagram", "fresh") is False


def test_commented_post_short_circuits(monkeypatch: pytest.MonkeyPatch, pg: _FakePg) -> None:
    """A post we already COMMENTED on is a duplicate even with Postgres empty."""
    monkeypatch.setattr(
        scan_dedup.deduplication,
        "already_commented",
        lambda platform, post_id: post_id == "commented",
    )
    dedup = ScanDedup("test-worker")

    assert dedup.is_duplicate("instagram", "commented") is True


def test_liked_but_uncommented_post_is_not_a_duplicate(
    monkeypatch: pytest.MonkeyPatch, pg: _FakePg
) -> None:
    """The retry path: a like alone must NOT make a post a duplicate.

    Single-pass likes before it comments, so if the comment submission fails
    the post carries a like mark. Gating on the presence-only
    `deduplication.is_duplicate` would make that post permanently ineligible
    and silently discard the retry that withholding the seen-mark provides.
    """
    monkeypatch.setattr(
        scan_dedup.deduplication,
        "is_duplicate",
        lambda platform, post_id: True,  # presence-only says "seen it"
    )
    monkeypatch.setattr(
        scan_dedup.deduplication,
        "already_commented",
        lambda platform, post_id: False,  # ...but we never commented
    )
    dedup = ScanDedup("test-worker")

    assert dedup.is_duplicate("instagram", "liked_then_comment_failed") is False


def test_a_second_platform_triggers_its_own_prefetch(pg: _FakePg) -> None:
    """The set is per-platform, so switching platform refetches once."""
    dedup = ScanDedup("test-worker")
    dedup.is_duplicate("instagram", "p1")
    dedup.is_duplicate("instagram", "p2")
    dedup.is_duplicate("facebook", "p3")

    assert pg.fetch_calls == [("scan", "instagram"), ("scan", "facebook")]


# --- marks feed back into the prefetched set ---------------------------------


def test_marking_seen_writes_through_and_updates_the_set(pg: _FakePg) -> None:
    """A newly marked post is a duplicate immediately, without a refetch."""
    dedup = ScanDedup("test-worker")
    assert dedup.is_duplicate("instagram", "p1") is False

    dedup.mark_seen("instagram", "p1")

    assert pg.write_calls == [("scan", "instagram", "p1")]
    assert dedup.is_duplicate("instagram", "p1") is True
    assert len(pg.fetch_calls) == 1, "marking must not trigger a refetch"


# --- degrade, never abort ----------------------------------------------------


def test_a_dead_database_degrades_to_the_json_cache(pg: _FakePg) -> None:
    """Losing Postgres mid-scan costs re-visits next run, not the run."""
    pg.fetch_should_fail = True
    dedup = ScanDedup("test-worker")

    assert dedup.is_duplicate("instagram", "p1") is False


def test_a_failed_write_still_blocks_a_repeat_within_the_run(pg: _FakePg) -> None:
    """We visited the post; nothing later in THIS run should re-open it."""
    pg.write_should_fail = True
    dedup = ScanDedup("test-worker")
    dedup.mark_seen("instagram", "p1")

    assert dedup.is_duplicate("instagram", "p1") is True


def test_the_database_warning_is_logged_once_per_run(
    pg: _FakePg, caplog: pytest.LogCaptureFixture
) -> None:
    """A dead DB must not spam a line per post."""
    pg.fetch_should_fail = True
    pg.write_should_fail = True
    dedup = ScanDedup("test-worker")

    with caplog.at_level("WARNING"):
        dedup.is_duplicate("instagram", "p1")
        for i in range(5):
            dedup.mark_seen("instagram", f"p{i}")

    warnings = [r for r in caplog.records if "dedup_pg_unavailable" in r.message]
    assert len(warnings) == 1
