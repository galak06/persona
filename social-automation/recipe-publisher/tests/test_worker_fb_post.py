"""Tests for the FB post worker: predicate, task, dry-run, and idempotency.

Uses a real temp sqlite DB (predicates ARE the product) with the heavy
collaborator (publish_link_post_to_facebook) monkeypatched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_fb_post as w


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _seed_row(
    repo: RecipeRepository,
    name: str,
    *,
    image_created_at: str = "",
    wp_url: str = "",
    fb_url: str = "",
) -> str:
    """Insert a minimal recipe row and return its id."""
    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    repo.upsert_recipe(row)
    rid = row.ensure_id()
    if image_created_at:
        repo.set_image_created_at(rid, image_created_at)
    if wp_url or fb_url:
        status: dict[str, dict[str, str]] = {}
        if wp_url:
            status["wp"] = {"state": "published", "url": wp_url, "ref": "1"}
        if fb_url:
            status["fb"] = {"state": "published", "url": fb_url, "ref": "p_1"}
        repo.set_publish_status(rid, status)
    return rid


@dataclass
class _FakeFBResult:
    post_id: str = "fb_post_123"
    permalink: str | None = "https://facebook.com/page/posts/fb_post_123"
    warnings: list[str] = field(default_factory=list)
    comment_id: str | None = None
    comment_warnings: list[str] = field(default_factory=list)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Patch the FB publisher, campaign_folder, and _find_existing_fb_post with fakes."""
    calls: dict[str, list] = {"fb": []}

    def _fake_publish(*, message: str, link: str, **kwargs: object) -> _FakeFBResult:
        calls["fb"].append({"message": message, "link": link})
        return _FakeFBResult()

    monkeypatch.setattr(w, "publish_link_post_to_facebook", _fake_publish)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    monkeypatch.setattr(w, "_find_existing_fb_post", lambda wp_url: None)
    return calls


# ----------------------------------------------------------------- predicate


def test_targets_eligible(tmp_path: Path) -> None:
    """Recipe with image_created_at set and fb_url empty is selected."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Chicken Rice Bowl", image_created_at="2026-06-17T10:00:00")
    ids = [r.id for r in w._targets(repo, [], 0)]
    assert ids == [rid]


def test_targets_skips_no_image_created_at(tmp_path: Path) -> None:
    """Recipe with empty image_created_at is NOT selected."""
    _, repo = _repo(tmp_path)
    _seed_row(repo, "Chicken Rice Bowl")  # no image_created_at
    assert w._targets(repo, [], 0) == []


def test_targets_skips_already_posted(tmp_path: Path) -> None:
    """Recipe with fb_url already set is NOT selected."""
    _, repo = _repo(tmp_path)
    _seed_row(
        repo,
        "Chicken Rice Bowl",
        image_created_at="2026-06-17T10:00:00",
        fb_url="https://facebook.com/page/posts/fb_post_123",
    )
    assert w._targets(repo, [], 0) == []


# --------------------------------------------------------------------- task


def test_do_one_publishes_and_saves(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    """Calls publish_link_post_to_facebook and saves fb_url in the DB."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        "Chicken Rice Bowl",
        image_created_at="2026-06-17T10:00:00",
        wp_url="https://example.com/chicken-rice-bowl",
    )
    row = repo.get_recipe(rid)
    assert row is not None

    outcome = w._do_one(repo, row)

    assert outcome == "fb_post"
    assert len(patched["fb"]) == 1
    assert patched["fb"][0]["link"] == "https://example.com/chicken-rice-bowl"

    updated = repo.get_recipe(rid)
    assert updated is not None
    assert updated.fb_url == "https://facebook.com/page/posts/fb_post_123"
    assert updated.publish_status["fb"]["state"] == "published"
    assert updated.publish_status["fb"]["ref"] == "fb_post_123"


def test_do_one_uses_caption_file(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    """Reads fb_caption.txt from campaign folder when it exists."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        "Chicken Rice Bowl",
        image_created_at="2026-06-17T10:00:00",
        wp_url="https://example.com/chicken-rice-bowl",
    )

    # Write a caption file into the mocked campaign folder
    folder = tmp_path / "ready" / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fb_caption.txt").write_text("Custom caption from file", encoding="utf-8")

    row = repo.get_recipe(rid)
    assert row is not None
    w._do_one(repo, row)

    assert patched["fb"][0]["message"] == "Custom caption from file"


def test_do_one_dry_run_no_publish(tmp_path: Path) -> None:
    """Dry-run: targets are logged but publish is NOT called."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(
        repo, "Chicken Rice Bowl", image_created_at="2026-06-17T10:00:00"
    )

    # Run in dry-run mode (no --apply)
    result = w.main(["--limit", "5"])
    assert result == 0

    # fb_url must remain empty after dry-run
    row = repo.get_recipe(rid)
    assert row is not None
    assert row.fb_url == ""


def test_do_one_idempotent(tmp_path: Path, patched: dict[str, list]) -> None:
    """Re-running on a recipe with fb_url already set is a noop (not re-selected)."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        "Chicken Rice Bowl",
        image_created_at="2026-06-17T10:00:00",
        wp_url="https://example.com/chicken-rice-bowl",
    )
    row = repo.get_recipe(rid)
    assert row is not None

    # First run: publishes
    w._do_one(repo, row)
    assert len(patched["fb"]) == 1

    # After success the row no longer matches the predicate
    targets = w._targets(repo, [], 0)
    assert targets == []


def test_do_one_fb_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _find_existing_fb_post returns a permalink, worker saves it and returns 'fb_exists'."""
    _, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        "Chicken Rice Bowl",
        image_created_at="2026-06-17T10:00:00",
        wp_url="https://example.com/chicken-rice-bowl",
    )

    existing_url = "https://facebook.com/page/posts/existing_post_456"
    fb_calls: list[dict] = []

    def _fake_find(wp_url: str) -> str | None:
        return existing_url

    def _fake_publish(*, message: str, link: str, **kwargs: object) -> _FakeFBResult:
        fb_calls.append({"message": message, "link": link})
        return _FakeFBResult()

    monkeypatch.setattr(w, "_find_existing_fb_post", _fake_find)
    monkeypatch.setattr(w, "publish_link_post_to_facebook", _fake_publish)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)

    row = repo.get_recipe(rid)
    assert row is not None

    outcome = w._do_one(repo, row)

    assert outcome == "fb_exists"
    assert fb_calls == [], "publish_link_post_to_facebook must NOT be called"

    updated = repo.get_recipe(rid)
    assert updated is not None
    assert updated.fb_url == existing_url
    assert updated.publish_status["fb"]["state"] == "published"
    assert updated.publish_status["fb"]["url"] == existing_url
    assert updated.publish_status["fb"]["ref"] == ""
