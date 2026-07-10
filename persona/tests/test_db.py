"""Tests for `lib/db.py` (Postgres connection-pool wrapper) and `db/schema.sql`.

The schema-content test always runs (pure text parsing, no infra needed).
The round-trip tests are real integration tests against a live Postgres —
they run when one is reachable at `DATABASE_URL` (or `lib.db_pool`'s local
dev default) and skip cleanly otherwise, e.g. in this sandbox with no
Postgres, or on a contributor's machine before `docker compose up postgres`.
CI provides a `postgres:16` service container with `DATABASE_URL` set, so
they run for real there.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from lib import db, db_pool

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_EXPECTED_TABLES = {"schedule_tasks", "worker_runs", "engagements", "brands", "fb_groups"}
_EXCLUDED_TABLES = {"recipes", "content_ideas", "oauth_tokens", "raw_scrapes", "completed_tasks"}


def _postgres_reachable() -> bool:
    """Best-effort connectivity probe, used to skip DB tests when none is available."""
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


# --------------------------------------------------------------------------- schema.sql content


def test_schema_file_scopes_only_the_pr1_tables() -> None:
    """`db/schema.sql` must define exactly the PR1 tables — no recipes_db/oauth tables."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    assert created == _EXPECTED_TABLES
    assert not created & _EXCLUDED_TABLES


def test_schema_file_has_no_supabase_specific_constructs() -> None:
    """Must be plain Postgres SQL — mountable at /docker-entrypoint-initdb.d/ as-is."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    lowered = sql.lower()
    assert "row level security" not in lowered
    assert "enable row level security" not in lowered
    assert "create policy" not in lowered
    assert "auth.uid()" not in lowered


# --------------------------------------------------------------------------- live Postgres round-trip


@pytest.fixture
def pg() -> Iterator[None]:
    """Apply schema.sql (idempotent), yield, then truncate the tables this module touched."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute(
            "TRUNCATE TABLE fb_groups, engagements, worker_runs, schedule_tasks, brands CASCADE"
        )


@requires_postgres
def test_execute_fetch_one_fetch_all_round_trip(pg: None) -> None:
    db.execute("INSERT INTO brands (id, name) VALUES (%s, %s)", ("acme", "Acme"))
    row = db.fetch_one("SELECT * FROM brands WHERE id = %s", ("acme",))
    assert row is not None
    assert row["name"] == "Acme"
    assert row["persona"] == ""  # column default

    rows = db.fetch_all("SELECT * FROM brands ORDER BY id")
    assert len(rows) == 1
    assert rows[0]["id"] == "acme"


@requires_postgres
def test_groups_round_trip_matches_pr1_verification_2(pg: None) -> None:
    """Mirrors PR1 verification #2: create a brand row, upsert a group, read it back."""
    db.execute(
        "INSERT INTO brands (id, name) VALUES (%s, %s)",
        ("dogfoodandfun", "Dog Food & Fun"),
    )
    db.execute(
        """
        INSERT INTO fb_groups (id, brand_id, group_url, group_name, status)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
        """,
        ("g1", "dogfoodandfun", "https://facebook.com/groups/x", "Test Group", "joined"),
    )
    row = db.fetch_one(
        "SELECT * FROM fb_groups WHERE group_url = %s", ("https://facebook.com/groups/x",)
    )
    assert row is not None
    assert row["group_name"] == "Test Group"
    assert row["status"] == "joined"
    assert row["brand_id"] == "dogfoodandfun"


@requires_postgres
def test_fb_groups_brand_id_fk_is_enforced(pg: None) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO fb_groups (id, brand_id, group_url) VALUES (%s, %s, %s)",
            ("orphan", "no-such-brand", "https://facebook.com/groups/orphan"),
        )


@requires_postgres
def test_get_connection_commits_on_success_rolls_back_on_exception(pg: None) -> None:
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO brands (id, name) VALUES (%s, %s)", ("committed", "Committed"))
    assert db.fetch_one("SELECT * FROM brands WHERE id = %s", ("committed",)) is not None

    with pytest.raises(RuntimeError), db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO brands (id, name) VALUES (%s, %s)", ("rolledback", "Nope"))
        raise RuntimeError("boom")
    assert db.fetch_one("SELECT * FROM brands WHERE id = %s", ("rolledback",)) is None


@requires_postgres
def test_health_check_true_when_reachable(pg: None) -> None:
    assert db.health_check() is True


# --------------------------------------------------------------------------- failure path (no infra needed)


def test_health_check_false_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nouser@localhost:1/doesnotexist")
    db_pool.close_pool()
    try:
        assert db.health_check() is False
    finally:
        db_pool.close_pool()
