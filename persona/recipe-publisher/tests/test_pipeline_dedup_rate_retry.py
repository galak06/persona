# pyright: reportMissingImports=false
"""Unit tests for the dedup (P6), rate-limit (P7), and retry (P9) gates."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline.dedup_check import DedupChecker
from pipeline.rate_limiting import RateLimitGate
from pipeline.retry import RetryExhaustedError, retry_call
from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository


# ----------------------------------------------------------------- rate limit
def test_rate_gate_remaining_and_allow() -> None:
    gate = RateLimitGate({"ig": 2})
    history = [("ig", "2026-06-12")]
    assert gate.used("ig", "2026-06-12", history) == 1
    assert gate.remaining("ig", "2026-06-12", history) == 1
    assert gate.allow("ig", "2026-06-12", history) is True


def test_rate_gate_blocks_at_cap() -> None:
    assert RateLimitGate({"ig": 1}).allow("ig", "d", [("ig", "d")]) is False


def test_rate_gate_unknown_platform_has_zero_cap() -> None:
    assert RateLimitGate({"ig": 5}).allow("tiktok", "d", []) is False


# ---------------------------------------------------------------------- retry
def test_retry_success_first_try() -> None:
    assert retry_call(lambda: 7) == (7, 1)


def test_retry_transient_then_success() -> None:
    state = {"n": 0}

    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert retry_call(flaky, attempts=5) == ("ok", 3)


def test_retry_permanent_propagates_immediately() -> None:
    def boom() -> None:
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        retry_call(boom, is_transient=lambda e: not isinstance(e, ValueError))


def test_retry_exhausted() -> None:
    def always() -> None:
        raise ConnectionError("nope")

    with pytest.raises(RetryExhaustedError) as exc:
        retry_call(always, attempts=2)
    assert exc.value.attempts == 2


# ---------------------------------------------------------------------- dedup
def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def test_dedup_flags_externally_published(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Dup Slug", content_hash="d1"))
        repo.upsert_recipe(RecipeRow(name="Fresh", content_hash="d2"))
        repo.set_content_status("dup-slug", ContentStatus.APPROVED)
        repo.set_content_status("fresh", ContentStatus.APPROVED)
        report = DedupChecker(repo, published_slugs={"dup-slug"}).run(persist=True)
        assert report.duplicates == 1
        assert report.unique_ids == ["fresh"]
        dup = repo.get_recipe("dup-slug")
        assert dup is not None and dup.content_status == ContentStatus.REJECTED
    finally:
        conn.close()
