"""Tests for publish-status sync from campaign + published records."""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from recipe_db import db, publish_sync
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository


def _repo(tmp_path: Path) -> RecipeRepository:
    conn = db.connect(tmp_path / "r.db")
    db.migrate(conn)
    return RecipeRepository(conn)


def test_build_publish_status_ties_pdf_to_wp() -> None:
    record: dict[str, object] = {
        "wp_live_url": "https://site/post/",
        "wp_post_id": 100,
        "fb_page_post_id": "abc",
        "fb_page_post_permalink": "https://fb/abc",
        "published_at": "2026-05-01T00:00:00Z",
    }
    status = publish_sync.build_publish_status(record)
    assert status["wp"]["state"] == "published"
    assert status["pdf"]["state"] == "published"  # mirrors WP recipe card
    assert status["fb"]["state"] == "published"
    assert status["ig"]["state"] == ""  # no IG fields present


def test_collect_records_from_both_sources(tmp_path: Path) -> None:
    folder = tmp_path / "campaigns" / "recipes" / "published" / "foo"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"seed_id": "foo", "wp_live_url": "https://site/foo/"})
    )
    pub = tmp_path / "published_recipes.json"
    pub.write_text(
        json.dumps(
            [{"slug": "bar", "wp_post_id": 5, "ig_media_id": "ig5"}]
        )
    )
    records = publish_sync.collect_publish_records(tmp_path / "campaigns", pub)
    assert records["foo"]["wp_live_url"] == "https://site/foo/"
    assert records["bar"]["ig_media_id"] == "ig5"


def test_sync_updates_only_matching_recipe(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.upsert_recipe(RecipeRow(name="Foo Treats", id="foo", content_hash="h-foo"))
    repo.upsert_recipe(
        RecipeRow(name="Other", id="other", content_hash="h-other")
    )
    folder = tmp_path / "campaigns" / "published" / "foo"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "seed_id": "foo",
                "wp_live_url": "https://site/foo/",
                "ig_reel_media_id": "ig1",
                "published_at": "2026-02-02",
            }
        )
    )
    updated = publish_sync.sync_publish_status(repo, tmp_path / "campaigns", None)
    assert updated == 1
    foo = repo.get_recipe("foo")
    assert foo is not None
    assert foo.publish_status["wp"]["state"] == "published"
    assert foo.publish_status["ig"]["state"] == "published"
    other = repo.get_recipe("other")
    assert other is not None
    assert other.publish_status == {}
