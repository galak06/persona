"""Tests for the FB groups DB: brand seeding + FK, round-trip fidelity
(including unmodeled `extra` keys and notes history), idempotent upsert, and the
structured setters.

Real integration tests against a live Postgres, following `test_db.py`'s
skipif pattern — they run when one is reachable at `DATABASE_URL` (or
`lib.db_pool`'s local dev default) and skip cleanly otherwise. CI provides a
`postgres:16` service container with `DATABASE_URL` set, so they run for real
there. This replaces the previous version of this file, which exercised a
SQLite `connect()`/`migrate()` API that no longer exists now that groups_db
runs on Postgres via `lib/db.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lib import db
from lib.groups_db.repository import GroupsRepository

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

_TREATS_URL = "https://www.facebook.com/groups/219924639809303/"
_RUN_URL = "https://www.facebook.com/groups/dogrunners/"

_SAMPLE: list[dict[str, Any]] = [
    {
        "group_name": "Homemade Pet Treats",
        "group_url": _TREATS_URL,
        "status": "joined",
        "joined_at": "2026-04-20T15:55:09Z",
        "posting_mode": "admins_only",
        "member_count": "91.6K",
        "self_promo_allowed": "yes",
        "notes": [{"at": "2026-04-20T16:47:30Z", "text": "Auto-classified"}],
        "weird_extra_key": "keepme",  # not in GROUP_COLUMNS -> extra
    },
    {
        "group_name": "Dog Runners",
        "group_url": _RUN_URL,
        "status": "join_requested",
    },
]


def _postgres_reachable() -> bool:
    """Best-effort connectivity probe, used to skip DB tests when none is available."""
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[GroupsRepository]:
    """Apply schema.sql (idempotent), point BRAND_DIR at a throwaway brand,
    yield a fresh repository, then truncate the tables this module touched.
    """
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    monkeypatch.setenv("BRAND_DIR", "/tmp/test-groups-db-brand")
    try:
        yield GroupsRepository()
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


@requires_postgres
def test_ensure_brand_seeds_brand_and_enforces_fk(repo: GroupsRepository) -> None:
    bid = repo.ensure_brand()
    row = db.fetch_one("SELECT * FROM brands WHERE id = %s", (bid,))
    assert row is not None
    count_row = db.fetch_one("SELECT count(*) AS n FROM brands")
    assert count_row is not None
    assert count_row["n"] == 1

    repo.save_all([dict(_SAMPLE[0])])
    joined = db.fetch_one(
        "SELECT b.id FROM fb_groups g JOIN brands b ON g.brand_id = b.id WHERE g.brand_id = %s",
        (bid,),
    )
    assert joined is not None


@requires_postgres
def test_save_all_then_load_all_round_trips(repo: GroupsRepository) -> None:
    repo.save_all([dict(g) for g in _SAMPLE])
    out = {g["group_url"]: g for g in repo.load_all()}
    assert len(out) == 2

    treats = out[_TREATS_URL]
    assert treats["status"] == "joined"
    assert treats["posting_mode"] == "admins_only"
    assert treats["member_count"] == "91.6K"
    assert treats["self_promo_allowed"] == "yes"
    assert treats["notes"] == [{"at": "2026-04-20T16:47:30Z", "text": "Auto-classified"}]
    assert treats["weird_extra_key"] == "keepme"  # extra round-trips

    # Absent/empty fields are not emitted as spurious keys.
    run = out[_RUN_URL]
    assert "last_post_at" not in run
    assert run["status"] == "join_requested"


@requires_postgres
def test_upsert_is_idempotent_by_url(repo: GroupsRepository) -> None:
    repo.save_all([dict(_SAMPLE[0])])
    repo.save_all([dict(_SAMPLE[0])])
    count_row = db.fetch_one("SELECT count(*) AS n FROM fb_groups")
    assert count_row is not None
    assert count_row["n"] == 1


@requires_postgres
def test_set_status_and_posting_mode(repo: GroupsRepository) -> None:
    repo.save_all([dict(_SAMPLE[1])])
    assert repo.set_status(_RUN_URL, "joined") is True
    assert repo.set_posting_mode(_RUN_URL, "direct") is True
    g = repo.get_by_url(_RUN_URL)
    assert g is not None
    assert g["status"] == "joined"
    assert g["posting_mode"] == "direct"
    # other fields untouched by the selective setter
    assert g["group_name"] == "Dog Runners"
    assert repo.set_status("https://nope/", "joined") is False


@requires_postgres
def test_append_note_preserves_history(repo: GroupsRepository) -> None:
    repo.save_all([dict(_SAMPLE[0])])
    repo.append_note(_TREATS_URL, {"at": "2026-06-14T00:00:00Z", "text": "second"})
    g = repo.get_by_url(_TREATS_URL)
    assert g is not None
    assert len(g["notes"]) == 2
    assert g["notes"][-1]["text"] == "second"


@requires_postgres
def test_append_note_returns_false_for_missing_group(repo: GroupsRepository) -> None:
    assert repo.append_note("https://nope/", {"at": "x", "text": "y"}) is False


@requires_postgres
def test_list_groups_filters_by_status(repo: GroupsRepository) -> None:
    repo.save_all([dict(g) for g in _SAMPLE])
    assert len(repo.list_groups("joined")) == 1
    assert len(repo.list_groups()) == 2


@requires_postgres
def test_get_by_name(repo: GroupsRepository) -> None:
    repo.save_all([dict(_SAMPLE[1])])
    g = repo.get_by_name("Dog Runners")
    assert g is not None
    assert g["group_url"] == _RUN_URL
    assert repo.get_by_name("No Such Group") is None


# --------------------------------------- retired first-comment gate columns
#
# The fb-engager first-comment approval gate was removed on 2026-08-13 (FB now
# matches ig-engager: the drafter's engage/decline decision is the only gate),
# along with its two setters. The COLUMNS stay -- they hold the historical
# stamps of groups that were flagged/approved while the gate existed -- so the
# round-trip guard below still has a job: every tracker writer does a
# load_all -> save_all, which must not reset those stored values or smuggle
# them into `extra`. Seeded with raw SQL now that the setters are gone.

_FLAGGED_AT = "2026-08-01T10:00:00+00:00"
_APPROVED_AT = "2026-08-01T11:00:00+00:00"


def _seed_gate_columns(group_url: str) -> None:
    """Stamp both retired gate columns directly (no setters exist anymore)."""
    db.execute(
        "UPDATE fb_groups SET first_comment_flagged_at = %s, "
        "first_comment_approved_at = %s WHERE group_url = %s",
        (_FLAGGED_AT, _APPROVED_AT, group_url),
    )


@requires_postgres
def test_gate_columns_emitted_only_when_set(repo: GroupsRepository) -> None:
    """load_all/_row_to_dict emit the gate columns when set, and never as
    spurious empty keys on untouched groups (same rule as GROUP_COLUMNS)."""
    repo.save_all([dict(g) for g in _SAMPLE])
    _seed_gate_columns(_TREATS_URL)

    out = {g["group_url"]: g for g in repo.load_all()}
    treats = out[_TREATS_URL]
    assert treats["first_comment_flagged_at"] == _FLAGGED_AT
    assert treats["first_comment_approved_at"] == _APPROVED_AT
    run = out[_RUN_URL]
    assert "first_comment_flagged_at" not in run
    assert "first_comment_approved_at" not in run


@requires_postgres
def test_gate_columns_survive_a_save_all_round_trip(repo: GroupsRepository) -> None:
    """The clobber guard: a load_all -> save_all round trip (every tracker
    writer does this) must neither reset the gate columns -- they are
    deliberately absent from GROUP_COLUMNS -- nor smuggle them into `extra`,
    where a later upsert would resurrect stale values past the setters."""
    repo.save_all([dict(_SAMPLE[0])])
    _seed_gate_columns(_TREATS_URL)

    repo.save_all(repo.load_all())  # round-trip the stored records verbatim

    g = repo.get_by_url(_TREATS_URL)
    assert g is not None
    assert g["first_comment_flagged_at"] == _FLAGGED_AT
    assert g["first_comment_approved_at"] == _APPROVED_AT

    row = db.fetch_one("SELECT extra FROM fb_groups WHERE group_url = %s", (_TREATS_URL,))
    assert row is not None
    raw_extra = row["extra"] or {}
    extra: dict[str, Any] = json.loads(raw_extra) if isinstance(raw_extra, str) else raw_extra
    assert "first_comment_flagged_at" not in extra
    assert "first_comment_approved_at" not in extra
    assert extra.get("weird_extra_key") == "keepme"  # real extras still round-trip
