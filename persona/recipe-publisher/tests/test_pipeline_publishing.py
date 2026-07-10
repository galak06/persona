# pyright: reportMissingImports=false
"""Tests for the publishing phase (P8) + analytics rollup (P10).

Exercises the full publish path with injected fake publishers: dry-run records
without calling, real publish marks PUBLISHED, the rate gate skips over-cap
platforms, and the retry loop recovers transient failures / records permanent
ones. No real platform calls.
"""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.analytics import AnalyticsTracker
from pipeline.publishing import PublishOrchestrator
from pipeline.rate_limiting import RateLimitGate
from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository


class _FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def publish(self, platform: str, row: RecipeRow) -> dict[str, str]:
        self.calls.append((platform, row.id))
        return {"ref": f"{platform}-{row.id}", "url": f"https://x/{platform}"}


class _FlakyPublisher:
    """Raises a transient error ``fails`` times per (platform, id) then succeeds."""

    def __init__(self, fails: int) -> None:
        self._fails = fails
        self._seen: dict[tuple[str, str], int] = {}

    def publish(self, platform: str, row: RecipeRow) -> dict[str, str]:
        key = (platform, row.id)
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] <= self._fails:
            raise ConnectionError("transient")
        return {"ref": "ok", "url": "u"}


class _DeadPublisher:
    def publish(self, platform: str, row: RecipeRow) -> dict[str, str]:
        raise ConnectionError("always down")


def _seed(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    repo = RecipeRepository(conn)
    repo.upsert_recipe(RecipeRow(name="Recipe One", dog_safe=True, content_hash="p1"))
    repo.set_content_status("recipe-one", ContentStatus.APPROVED)
    return conn, repo


def test_dry_run_records_without_publishing(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    pub = _FakePublisher()
    try:
        report = PublishOrchestrator(repo, publisher=pub, dry_run=True).run(
            today="2026-06-12"
        )
        assert report.published == 0
        assert pub.calls == []  # never called in dry-run
        row = repo.get_recipe("recipe-one")
        assert row is not None
        assert row.content_status == ContentStatus.APPROVED  # not published
        assert any(r["status"] == "dry_run" for r in row.publish_results)
        analytics = AnalyticsTracker(repo).run()
        assert analytics.by_status.get("dry_run", 0) >= 1
    finally:
        conn.close()


def test_real_publish_marks_published(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    pub = _FakePublisher()
    try:
        report = PublishOrchestrator(
            repo, publisher=pub, dry_run=False, platforms=("ig", "fb")
        ).run(today="2026-06-12")
        assert report.published == 2
        assert len(pub.calls) == 2
        row = repo.get_recipe("recipe-one")
        assert row is not None
        assert row.content_status == ContentStatus.PUBLISHED
    finally:
        conn.close()


def test_rate_limit_skips_over_cap(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = PublishOrchestrator(
            repo,
            publisher=_FakePublisher(),
            rate_gate=RateLimitGate({"ig": 0}),
            platforms=("ig",),
            dry_run=False,
        ).run(today="2026-06-12")
        assert report.skipped_rate_limited == 1
        assert report.published == 0
    finally:
        conn.close()


def test_retry_recovers_transient(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = PublishOrchestrator(
            repo,
            publisher=_FlakyPublisher(fails=1),
            platforms=("ig",),
            dry_run=False,
            attempts=3,
        ).run(today="2026-06-12")
        assert report.published == 1
        row = repo.get_recipe("recipe-one")
        assert row is not None
        ig = next(r for r in row.publish_results if r["platform"] == "ig")
        assert ig["status"] == "published"
        assert ig["attempts"] == "2"
    finally:
        conn.close()


def test_permanent_failure_recorded(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = PublishOrchestrator(
            repo,
            publisher=_DeadPublisher(),
            platforms=("ig",),
            dry_run=False,
            attempts=2,
        ).run(today="2026-06-12")
        assert report.failed == 1
        assert report.published == 0
        row = repo.get_recipe("recipe-one")
        assert row is not None
        assert row.content_status == ContentStatus.APPROVED  # never published
        ig = next(r for r in row.publish_results if r["platform"] == "ig")
        assert ig["status"] == "failed"
    finally:
        conn.close()
