"""Tests for `lib/comment_outbox.py` (comment_claims table via `lib/db.py`).

Real integration tests against a live local Postgres, following
`test_dedup_pg.py`'s fixture pattern. The behaviours asserted here are the
ones the duplicate-comment bug of 2026-08 turned on: a claim is exclusive, a
settled claim is permanent, and nothing but an outright unique violation is
allowed to look like "someone else already has it".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from lib import comment_outbox, db

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

_BRAND = "outbox-brand"


from tests._pg import requires_postgres


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE comment_claims")


def _backdate(post_id: str, minutes: int, brand: str = _BRAND) -> None:
    """Age a claim so `pending_older_than` can see it without a real wait."""
    db.execute(
        """
        UPDATE comment_claims
        SET claimed_at = NOW() - make_interval(mins => %s)
        WHERE brand = %s AND post_id = %s
        """,
        (minutes, brand, post_id),
    )


def _claim_row(post_id: str, brand: str = _BRAND) -> dict[str, Any]:
    row = db.fetch_one(
        "SELECT status, settled_at FROM comment_claims WHERE brand = %s AND post_id = %s",
        (brand, post_id),
    )
    assert row is not None
    return row


# --------------------------------------------------------------------------- claim


@requires_postgres
def test_claim_returns_true_first_time_false_second(pg: None) -> None:
    assert comment_outbox.claim("instagram", "post-1", brand=_BRAND) is True
    assert comment_outbox.claim("instagram", "post-1", brand=_BRAND) is False


@requires_postgres
def test_claim_is_scoped_by_brand(pg: None) -> None:
    assert comment_outbox.claim("instagram", "shared-post", brand="brand-a") is True
    assert comment_outbox.claim("instagram", "shared-post", brand="brand-b") is True


@requires_postgres
def test_claim_is_scoped_by_platform(pg: None) -> None:
    assert comment_outbox.claim("instagram", "post-2", brand=_BRAND) is True
    assert comment_outbox.claim("facebook", "post-2", brand=_BRAND) is True


# --------------------------------------------------------------------------- settle


@requires_postgres
def test_settle_moves_pending_to_posted_and_stamps_settled_at(pg: None) -> None:
    comment_outbox.claim("instagram", "post-3", brand=_BRAND)
    row = _claim_row("post-3")
    assert row["status"] == "pending"
    assert row["settled_at"] is None

    assert comment_outbox.settle("instagram", "post-3", brand=_BRAND) is True

    row = _claim_row("post-3")
    assert row["status"] == "posted"
    assert row["settled_at"] is not None


@requires_postgres
def test_settle_is_idempotent_and_preserves_first_settled_at(pg: None) -> None:
    comment_outbox.claim("instagram", "post-4", brand=_BRAND)
    assert comment_outbox.settle("instagram", "post-4", brand=_BRAND) is True
    first = _claim_row("post-4")["settled_at"]

    assert comment_outbox.settle("instagram", "post-4", brand=_BRAND) is True
    assert _claim_row("post-4")["settled_at"] == first


# --------------------------------------------------------------------------- release


@requires_postgres
def test_release_deletes_a_pending_claim_and_reopens_the_post(pg: None) -> None:
    assert comment_outbox.claim("instagram", "post-5", brand=_BRAND) is True
    assert comment_outbox.release("instagram", "post-5", brand=_BRAND) is True
    assert comment_outbox.claim("instagram", "post-5", brand=_BRAND) is True


@requires_postgres
def test_release_refuses_to_delete_a_posted_claim(pg: None) -> None:
    """The safety rail: release can never be what makes us comment twice."""
    comment_outbox.claim("instagram", "post-6", brand=_BRAND)
    comment_outbox.settle("instagram", "post-6", brand=_BRAND)

    assert comment_outbox.release("instagram", "post-6", brand=_BRAND) is False
    assert "post-6" in comment_outbox.claimed_post_ids("instagram", brand=_BRAND)
    assert comment_outbox.claim("instagram", "post-6", brand=_BRAND) is False


# --------------------------------------------------------------------------- claimed_post_ids


@requires_postgres
def test_claimed_post_ids_returns_pending_and_posted(pg: None) -> None:
    comment_outbox.claim("instagram", "pending-post", brand=_BRAND)
    comment_outbox.claim("instagram", "posted-post", brand=_BRAND)
    comment_outbox.settle("instagram", "posted-post", brand=_BRAND)
    comment_outbox.claim("facebook", "other-platform", brand=_BRAND)
    comment_outbox.claim("instagram", "other-brand", brand="somebody-else")

    assert comment_outbox.claimed_post_ids("instagram", brand=_BRAND) == {
        "pending-post",
        "posted-post",
    }


# --------------------------------------------------------------------------- pending_older_than


@requires_postgres
def test_pending_older_than_excludes_fresh_claims(pg: None) -> None:
    comment_outbox.claim("instagram", "fresh", brand=_BRAND)
    assert comment_outbox.pending_older_than(30, brand=_BRAND) == []


@requires_postgres
def test_pending_older_than_returns_stale_claims_oldest_first(pg: None) -> None:
    comment_outbox.claim(
        "instagram",
        "stale-newer",
        post_url="https://instagram.com/p/newer",
        target_name="#dogs",
        content="newer text",
        worker_label="ig-engager",
        brand=_BRAND,
    )
    comment_outbox.claim("instagram", "stale-older", content="older text", brand=_BRAND)
    _backdate("stale-newer", 45)
    _backdate("stale-older", 90)

    stale = comment_outbox.pending_older_than(30, brand=_BRAND)

    assert [c.post_id for c in stale] == ["stale-older", "stale-newer"]
    newer = stale[1]
    assert newer.brand == _BRAND
    assert newer.platform == "instagram"
    assert newer.post_url == "https://instagram.com/p/newer"
    assert newer.target_name == "#dogs"
    assert newer.content == "newer text"
    assert newer.worker_label == "ig-engager"
    assert newer.claimed_at is not None


@requires_postgres
def test_pending_older_than_excludes_posted(pg: None) -> None:
    comment_outbox.claim("instagram", "settled-and-old", brand=_BRAND)
    _backdate("settled-and-old", 120)
    comment_outbox.settle("instagram", "settled-and-old", brand=_BRAND)

    assert comment_outbox.pending_older_than(30, brand=_BRAND) == []


# --------------------------------------------------------------------------- fail-closed contract


def test_claim_propagates_non_unique_violation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only UniqueViolation means "taken". Everything else must stop the run.

    Returning False on, say, a dropped connection would read as "someone else
    owns this post" and silently skip it; worse, a caller that treats False as
    "already handled" would never notice the outbox was unreachable. A lost
    claim costs a duplicate comment, so this path fails closed and loud.
    """

    def _boom(query: str, params: object = None) -> int:
        raise psycopg.OperationalError("connection closed")

    monkeypatch.setattr(comment_outbox, "execute", _boom)

    with pytest.raises(psycopg.OperationalError):
        comment_outbox.claim("instagram", "post-7", brand=_BRAND)
