"""Tests for `scripts/backfill_comment_claims.py`.

Real integration tests against a live local Postgres, following
`test_comment_outbox.py`'s fixture pattern -- the guarantees under test are
about rows, so a mocked database would assert nothing worth knowing. The two
that matter most: the union preserves history the JSON dedup cache has already
clobbered, and a re-run can never touch a `pending` claim a running engager
owns (this repo is additive-only).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from scripts import backfill_comment_claims as bcc

from lib import db
from tests._pg import requires_postgres

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

_BRAND = "backfill-brand"


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        # Re-apply first: one test drops the table on purpose.
        db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
        db.execute("TRUNCATE TABLE comment_claims")
        db.execute("DELETE FROM completed_tasks WHERE brand = %s", (_BRAND,))


@pytest.fixture
def brand_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A brand directory with empty `logs/` and `state/`, pointed at by env."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", _BRAND)
    return tmp_path


def _write_log(brand_dir: Path, *entries: dict[str, Any]) -> None:
    (brand_dir / "logs" / "engagement_log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )


def _write_cache(brand_dir: Path, cache: dict[str, Any]) -> None:
    (brand_dir / "state" / "dedup_cache.json").write_text(json.dumps(cache), encoding="utf-8")


def _comment_entry(post_id: str, **overrides: Any) -> dict[str, Any]:
    entry = {
        "date": "2026-08-20",
        "timestamp": "2026-08-20T19:21:10.535113+00:00",
        "action": "comment",
        "platform": "instagram",
        "target_name": "rawdogfood",
        "content": "We tried this with Nalla and she loved it. How does yours handle it?",
        "post_url": f"https://www.instagram.com/p/{post_id}/",
        "post_id": post_id,
    }
    entry.update(overrides)
    return entry


def _rows() -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT * FROM comment_claims WHERE brand = %s ORDER BY platform, post_id",
        (_BRAND,),
    )


@requires_postgres
def test_seeds_posted_rows_from_the_engagement_log(pg: None, brand_dir: Path) -> None:
    _write_log(
        brand_dir,
        _comment_entry("Dblugy3Hd0U"),
        {"action": "like", "platform": "instagram", "post_id": "LikeOnly1"},
        # Pre-2026-08 comment line: no post_id, so nothing to claim.
        {"action": "comment", "platform": "instagram", "target_name": "dogsofinsta"},
    )

    stats = bcc.backfill(_BRAND)

    assert stats["engagement_log"] == 1
    assert stats["inserted"] == 1
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["post_id"] == "Dblugy3Hd0U"
    # Never `pending`: this is settled history, not work for the reconciler.
    assert rows[0]["status"] == "posted"
    assert rows[0]["worker_label"] == "backfill"
    assert rows[0]["post_url"] == "https://www.instagram.com/p/Dblugy3Hd0U/"
    assert rows[0]["target_name"] == "rawdogfood"
    assert "Nalla" in rows[0]["content"]
    assert rows[0]["settled_at"] is not None
    assert rows[0]["claimed_at"].year == 2026


@requires_postgres
def test_unions_completed_tasks_and_the_json_dedup_cache(pg: None, brand_dir: Path) -> None:
    _write_log(brand_dir, _comment_entry("FromLog"))
    db.execute(
        """
        INSERT INTO completed_tasks (task_type, platform, entity_id, brand, completed_at)
        VALUES ('comment', 'facebook', 'FromTasks', %s, '2026-08-01T10:00:00+00:00')
        """,
        (_BRAND,),
    )
    _write_cache(
        brand_dir,
        {
            "instagram": {
                # Only this one is a real comment mark; the others must be ignored.
                "FromCache": {
                    "engaged_at": "2026-07-02",
                    "action": "comment",
                    "status": "engaged",
                    "group_or_hashtag": "dogtreats",
                },
                "LikedOnly": {"engaged_at": "2026-07-02", "action": "like", "status": "engaged"},
                "FailedOne": {"engaged_at": "2026-07-02", "action": "comment", "status": "failed"},
            },
            "wordpress": {
                "wp-1": {"engaged_at": "2026-07-02", "action": "comment", "status": "engaged"}
            },
        },
    )

    stats = bcc.backfill(_BRAND)

    assert (stats["engagement_log"], stats["completed_tasks"], stats["dedup_cache"]) == (1, 1, 1)
    assert stats["unique"] == 3
    assert stats["inserted"] == 3
    assert {(r["platform"], r["post_id"]) for r in _rows()} == {
        ("instagram", "FromLog"),
        ("facebook", "FromTasks"),
        ("instagram", "FromCache"),
    }
    by_id = {r["post_id"]: r for r in _rows()}
    assert by_id["FromCache"]["target_name"] == "dogtreats"
    assert all(r["status"] == "posted" for r in _rows())


@requires_postgres
def test_is_idempotent_on_rerun(pg: None, brand_dir: Path) -> None:
    _write_log(brand_dir, _comment_entry("Repeat1"), _comment_entry("Repeat2"))

    first = bcc.backfill(_BRAND)
    second = bcc.backfill(_BRAND)

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["before"] == second["after"] == 2
    assert len(_rows()) == 2


@requires_postgres
def test_never_overwrites_an_existing_pending_claim(pg: None, brand_dir: Path) -> None:
    """A live engager owns this post right now -- the backfill must not settle it."""
    db.execute(
        """
        INSERT INTO comment_claims
            (brand, platform, post_id, status, content, worker_label)
        VALUES (%s, 'instagram', 'InFlight', 'pending', 'live draft', 'ig-engager')
        """,
        (_BRAND,),
    )
    _write_log(brand_dir, _comment_entry("InFlight", content="backfilled text"))

    stats = bcc.backfill(_BRAND)

    assert stats["inserted"] == 0
    row = _rows()[0]
    assert row["status"] == "pending"
    assert row["content"] == "live draft"
    assert row["worker_label"] == "ig-engager"
    assert row["settled_at"] is None


@requires_postgres
def test_refuses_the_default_brand(
    pg: None, brand_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["backfill_comment_claims.py", "--brand", "default"])

    assert bcc.main() == 2
    assert "default" in capsys.readouterr().err
    with pytest.raises(ValueError, match="default"):
        bcc.backfill("default")


@requires_postgres
def test_exits_with_a_clear_error_when_the_table_is_missing(
    pg: None, brand_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("DROP TABLE comment_claims")
    monkeypatch.setattr("sys.argv", ["backfill_comment_claims.py", "--dry-run"])

    assert bcc.main() == 2
    err = capsys.readouterr().err
    assert "comment_claims does not exist" in err
    assert "db/schema.sql" in err
