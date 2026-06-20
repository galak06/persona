"""Tests for Worker E (image): the DB-polling predicate, the image task, and
idempotency. Uses a real temp sqlite DB with the heavy collaborators
(image generation, rehydration, folder) monkeypatched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_image as w


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _seed_row(
    repo: RecipeRepository,
    name: str,
    *,
    wp_url: str = "",
    image_created_at: str = "",
) -> str:
    """Insert a recipe row and optionally set wp_url / image_created_at."""
    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    repo.upsert_recipe(row)
    rid = row.ensure_id()
    if wp_url:
        repo.set_publish_status(rid, {"wp": {"url": wp_url, "state": "published"}})
    if image_created_at:
        repo.set_image_created_at(rid, image_created_at)
    return rid


@dataclass
class _FakeRecipe:
    name: str
    image_brief: str
    seed_id: str = ""
    title: str = ""
    slug: str = ""
    ig_caption: str = ""


@dataclass
class _FakeImage:
    url: str = "img://fake"
    alt_text: str = ""
    provider: str = "fake"
    bytes_: bytes | None = b"FAKEIMGDATA"
    content_type: str = "image/jpeg"


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Patch Worker E's collaborators with deterministic fakes; record calls."""
    calls: dict[str, list] = {"generate_image": [], "rehydrate": []}

    def _rehydrate(row: RecipeRow) -> _FakeRecipe:
        calls["rehydrate"].append(row.id)
        return _FakeRecipe(
            name=row.name,
            image_brief=f"dog food recipe: {row.name}",
            seed_id=row.id,
            title=row.name,
            slug=row.id,
        )

    def _generate_image(brief: str, *, alt_hint: str) -> _FakeImage:
        calls["generate_image"].append({"brief": brief, "alt_hint": alt_hint})
        return _FakeImage()

    monkeypatch.setattr(w, "rehydrate_recipe", _rehydrate)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    monkeypatch.setattr("generators.image.generate_image", _generate_image)

    return calls


# ----------------------------------------------------------------- predicate

def test_targets_eligible(tmp_path: Path) -> None:
    """Recipe with wp_url set and image_created_at empty is selected."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp_url="https://example.com/beef-bowl")
    ids = [r.id for r in w._targets(repo, [], 0)]
    assert ids == [rid]


def test_targets_skips_no_wp_url(tmp_path: Path) -> None:
    """Recipe with empty wp_url is not selected."""
    _, repo = _repo(tmp_path)
    _seed_row(repo, "Beef Bowl")  # no wp_url
    assert w._targets(repo, [], 0) == []


def test_targets_skips_already_done(tmp_path: Path) -> None:
    """Recipe with image_created_at already set is not selected."""
    _, repo = _repo(tmp_path)
    _seed_row(
        repo,
        "Beef Bowl",
        wp_url="https://example.com/beef-bowl",
        image_created_at="2026-01-01T00:00:00",
    )
    assert w._targets(repo, [], 0) == []


# --------------------------------------------------------------------- task

def test_do_one_saves_image_and_sets_db(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    """do_one writes post_image.jpg to the folder and sets image_created_at in DB."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp_url="https://example.com/beef-bowl")

    outcome = w._do_one(repo, repo.get_recipe(rid))

    assert outcome == "image"

    img_path = tmp_path / "ready" / rid / "post_image.jpg"
    assert img_path.exists()
    assert img_path.read_bytes() == b"FAKEIMGDATA"

    row = repo.get_recipe(rid)
    assert row is not None
    assert row.image_created_at != ""
    assert len(patched["generate_image"]) == 1


def test_do_one_dry_run_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In dry-run mode generate_image is NOT called and no file is written."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp_url="https://example.com/beef-bowl")

    generate_calls: list[dict] = []

    def _boom(brief: str, *, alt_hint: str) -> None:
        generate_calls.append({"brief": brief, "alt_hint": alt_hint})
        raise AssertionError("generate_image must not be called in dry-run")

    monkeypatch.setattr("generators.image.generate_image", _boom)

    # Dry-run: check targets only, do NOT call _do_one.
    targets = w._targets(repo, [], 0)
    assert [r.id for r in targets] == [rid]

    img_path = tmp_path / "ready" / rid / "post_image.jpg"
    assert not img_path.exists()
    assert generate_calls == []


def test_do_one_idempotent(tmp_path: Path, patched: dict[str, list]) -> None:
    """After success, the row no longer matches the predicate."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl", wp_url="https://example.com/beef-bowl")

    w._do_one(repo, repo.get_recipe(rid))

    # Row is now stamped — predicate must not re-select it.
    assert w._targets(repo, [], 0) == []
