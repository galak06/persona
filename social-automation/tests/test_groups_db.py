"""Tests for the FB groups DB: brand seeding + FK, round-trip fidelity
(including unmodeled `extra` keys and notes history), idempotent upsert, and the
structured setters. Mirrors the recipe_db `_repo(tmp_path)` fixture pattern.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lib.groups_db.db import connect, migrate
from lib.groups_db.repository import GroupsRepository

_TREATS_URL = "https://www.facebook.com/groups/219924639809303/"
_RUN_URL = "https://www.facebook.com/groups/dogrunners/"

_SAMPLE = [
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


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, GroupsRepository]:
    conn = connect(tmp_path / "g.db")
    migrate(conn)
    return conn, GroupsRepository(conn)


def test_migrate_seeds_brand_and_enforces_fk(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.ensure_brand()
        assert conn.execute("SELECT count(*) FROM brands").fetchone()[0] == 1
        repo.save_all([dict(_SAMPLE[0])])
        # FK is satisfied: the group's brand_id matches the seeded brand.
        joined = conn.execute(
            "SELECT b.id FROM fb_groups g JOIN brands b ON g.brand_id = b.id"
        ).fetchone()
        assert joined is not None
    finally:
        conn.close()


def test_save_all_then_load_all_round_trips(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
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
    finally:
        conn.close()


def test_upsert_is_idempotent_by_url(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.save_all([dict(_SAMPLE[0])])
        repo.save_all([dict(_SAMPLE[0])])
        assert conn.execute("SELECT count(*) FROM fb_groups").fetchone()[0] == 1
    finally:
        conn.close()


def test_set_status_and_posting_mode(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
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
    finally:
        conn.close()


def test_append_note_preserves_history(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.save_all([dict(_SAMPLE[0])])
        repo.append_note(_TREATS_URL, {"at": "2026-06-14T00:00:00Z", "text": "second"})
        g = repo.get_by_url(_TREATS_URL)
        assert g is not None
        assert len(g["notes"]) == 2
        assert g["notes"][-1]["text"] == "second"
    finally:
        conn.close()


def test_list_groups_filters_by_status(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.save_all([dict(g) for g in _SAMPLE])
        assert len(repo.list_groups("joined")) == 1
        assert len(repo.list_groups()) == 2
    finally:
        conn.close()
