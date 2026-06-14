"""Tests for Worker B (post-images): the DB-polling predicate, the generate-once
save-both-variants task, and idempotency. Real temp sqlite DB; the carousel /
seed / image collaborators are monkeypatched (no LLM or image API calls).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_post_images as w


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _seed_row(repo: RecipeRepository, name: str, *, wp: bool) -> str:
    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    repo.upsert_recipe(row)
    rid = row.ensure_id()
    if wp:
        repo.set_publish_status(rid, {"wp": {"url": f"https://x/{rid}"}})
    return rid


@dataclass
class _FakeImg:
    bytes_: bytes


@dataclass
class _FakeSeed:
    id: str


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch Worker B's seed/carousel/image collaborators with fakes."""
    monkeypatch.setattr(
        "generators.seeds.load_seeds",
        lambda: [_FakeSeed(id="beef-bowl"), _FakeSeed(id="apple-crunch")],
    )
    monkeypatch.setattr(
        "generators.carousel_drafter.ensure_carousel_json",
        lambda seed, *, force=False: tmp_path / f"{seed.id}.json",
    )
    monkeypatch.setattr(
        "generators.carousel.generate_post_and_reel_slides",
        lambda *, seed_id, recipe_title, badge_path: (
            [_FakeImg(b"post1"), _FakeImg(b"post2")],
            [b"reel1", b"reel2"],
        ),
    )
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    monkeypatch.setattr(w, "badge_path", lambda: None)


# ----------------------------------------------------------------- predicate
def test_targets_needs_wp_without_slides(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    ready = _seed_row(repo, "Beef Bowl", wp=True)
    _seed_row(repo, "No WP Yet", wp=False)  # no wp_url → excluded
    assert [r.id for r in w._targets(repo, [], 0)] == [ready]


def test_targets_excludes_done(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp=True)
    repo.set_slides(rid, 4, "2026-06-14T00:00:00Z")  # already has slides
    assert w._targets(repo, [], 0) == []


# --------------------------------------------------------------------- task
def test_do_one_saves_both_variants_and_marks(
    tmp_path: Path, patched: None
) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp=True)

    assert w._do_one(repo, repo.get_recipe(rid)) == "slides=2"

    folder = tmp_path / "ready" / rid
    assert (folder / "slides" / "slide_1.jpg").read_bytes() == b"post1"
    assert (folder / "slides" / "slide_2.jpg").read_bytes() == b"post2"
    # Reel frames are the UN-badged variant, saved separately for Worker C.
    assert (folder / "reel_src" / "slide_1.jpg").read_bytes() == b"reel1"

    row = repo.get_recipe(rid)
    assert row is not None
    assert row.slides_count == 2
    assert row.slides_created_at  # timestamp recorded
    assert row.reel_created_at == ""  # Worker C's column untouched


def test_do_one_is_idempotent(tmp_path: Path, patched: None) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp=True)
    w._do_one(repo, repo.get_recipe(rid))
    assert w._targets(repo, [], 0) == []  # not re-selected after success


def test_do_one_no_seed_leaves_gate_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp=True)
    monkeypatch.setattr("generators.seeds.load_seeds", list)  # no seeds

    assert w._do_one(repo, repo.get_recipe(rid)) == "no-seed"
    assert repo.get_recipe(rid).slides_created_at == ""  # still selectable
