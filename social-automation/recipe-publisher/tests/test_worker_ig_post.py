"""Tests for Worker IG-post: DB-polling predicate, publish task, and idempotency.

Uses a real temp sqlite DB (the predicates ARE the product) with all external
collaborators (_html_to_png, _upload_image_to_wp, publish_to_instagram,
campaign_folder, rehydrate_recipe, _find_existing_ig_post) monkeypatched.

New behaviour (card_html_created_at era):
  Primary   — card_html_created_at truthy AND ig_url empty  (oldest first)
  Fallback  — card_html_created_at truthy AND ig_url set    (oldest first)
             used when no unposted recipes remain, enabling a repost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_ig_post as w

# ------------------------------------------------------------------ helpers


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _seed_row(
    repo: RecipeRepository,
    conn: sqlite3.Connection,
    name: str,
    *,
    card_html_created_at: str = "",
    ig_url: str = "",
    wp_url: str = "",
    wp_post_id: int | None = None,
    generated_content: dict | None = None,
) -> str:
    """Insert a minimal recipe row and return its id.

    Uses direct SQL for card_html_created_at so tests can supply exact ISO
    timestamps for ordering assertions (repo.set_card_html always stamps
    CURRENT_TIMESTAMP).
    """
    import json

    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    if generated_content:
        row.generated_content = generated_content
    repo.upsert_recipe(row)
    rid = row.ensure_id()

    status: dict[str, dict[str, str]] = {}
    if wp_url:
        status["wp"] = {"state": "published", "url": wp_url, "ref": "1", "at": ""}
    if ig_url:
        status["ig"] = {
            "state": "published",
            "url": ig_url,
            "ref": "ig123",
            "at": "",
        }
    if status:
        repo.set_publish_status(rid, status)
    if wp_post_id is not None:
        repo.set_wp_post_id(rid, wp_post_id)
    if generated_content:
        conn.execute(
            "UPDATE recipes SET generated_content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(generated_content), rid),
        )
        conn.commit()
    if card_html_created_at:
        conn.execute(
            "UPDATE recipes SET card_html_created_at = ?, card_html_path = 'dummy/card.html',"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (card_html_created_at, rid),
        )
        conn.commit()
    return rid


@dataclass
class _FakeRecipe:
    slug: str
    title: str
    seed_id: str = ""
    ig_caption: str = "Nalla loves this recipe \U0001f43e"
    tags: list[str] = field(default_factory=lambda: ["dog-treats"])


@dataclass
class _FakeIGResult:
    media_id: str = "ig_media_999"
    permalink: str | None = "https://www.instagram.com/p/fake999/"
    warnings: list[str] = field(default_factory=list)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Patch IG worker collaborators with deterministic fakes; record calls."""
    calls: dict[str, list] = {
        "rehydrate": [],
        "html_to_png": [],
        "upload_image_to_wp": [],
        "publish_to_instagram": [],
        "campaign_folder": [],
        "find_existing_ig_post": [],
    }

    def _rehydrate(row: RecipeRow) -> _FakeRecipe:
        calls["rehydrate"].append(row.id)
        return _FakeRecipe(slug=row.id, title=row.name, seed_id=row.id)

    def _html_to_png(html_path: Path, out_path: Path) -> None:
        calls["html_to_png"].append(str(html_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-png")

    def _upload_image_to_wp(png_path: Path) -> str:
        calls["upload_image_to_wp"].append(str(png_path))
        return "https://dogfoodandfun.com/wp-content/uploads/card.png"

    def _publish(recipe: object, *, image_url: str) -> _FakeIGResult:
        calls["publish_to_instagram"].append(
            {"slug": getattr(recipe, "slug", "?"), "image_url": image_url}
        )
        return _FakeIGResult()

    def _campaign_folder(row: RecipeRow) -> Path:
        calls["campaign_folder"].append(row.id)
        folder = tmp_path / "ready" / row.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "post_image_card.html").write_text("<html><body></body></html>", encoding="utf-8")
        return folder

    def _find_existing(recipe_name: str, wp_url: str) -> str | None:
        calls["find_existing_ig_post"].append((recipe_name, wp_url))
        return None  # default: no existing post

    monkeypatch.setattr(w, "rehydrate_recipe", _rehydrate)
    monkeypatch.setattr(w, "_html_to_png", _html_to_png)
    monkeypatch.setattr(w, "_upload_image_to_wp", _upload_image_to_wp)
    monkeypatch.setattr(w, "publish_to_instagram", _publish)
    monkeypatch.setattr(w, "campaign_folder", _campaign_folder)
    monkeypatch.setattr(w, "_find_existing_ig_post", _find_existing)
    return calls


# ----------------------------------------------------------------- predicate


def test_targets_eligible(tmp_path: Path) -> None:
    """Recipe with card_html_created_at set and ig_url empty is selected by primary query."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(repo, conn, "Chicken Bites", card_html_created_at="2024-01-01T10:00:00")
    ids = [r.id for r in w._targets(repo, [], 0)]
    assert rid in ids


def test_targets_skips_no_card_html(tmp_path: Path) -> None:
    """Recipe with no card_html_created_at is never returned by primary or fallback."""
    conn, repo = _repo(tmp_path)
    _seed_row(repo, conn, "Chicken Bites")  # no card_html_created_at
    assert w._targets(repo, [], 0) == []


def test_targets_prefers_unposted(tmp_path: Path) -> None:
    """With one posted and one unposted recipe, primary query selects only the unposted one."""
    conn, repo = _repo(tmp_path)
    rid_posted = _seed_row(
        repo,
        conn,
        "Already Posted",
        card_html_created_at="2024-01-01T10:00:00",
        ig_url="https://www.instagram.com/p/already/",
    )
    rid_fresh = _seed_row(
        repo,
        conn,
        "Fresh Recipe",
        card_html_created_at="2024-01-02T10:00:00",
    )
    ids = [r.id for r in w._targets(repo, [], 0)]
    assert rid_fresh in ids
    assert rid_posted not in ids


def test_targets_fallback_oldest_when_all_posted(tmp_path: Path) -> None:
    """When all recipes are posted, fallback selects the one with the oldest card_html_created_at."""
    conn, repo = _repo(tmp_path)
    rid_older = _seed_row(
        repo,
        conn,
        "Older Recipe",
        card_html_created_at="2024-01-01T10:00:00",
        ig_url="https://www.instagram.com/p/older/",
    )
    rid_newer = _seed_row(
        repo,
        conn,
        "Newer Recipe",
        card_html_created_at="2024-06-01T10:00:00",
        ig_url="https://www.instagram.com/p/newer/",
    )
    result = w._targets(repo, [], 0)
    assert len(result) >= 1
    assert result[0].id == rid_older
    assert rid_newer not in [r.id for r in result[:1]]


# ----------------------------------------------------------------------- task


def test_do_one_publishes_and_saves(tmp_path: Path, patched: dict[str, list]) -> None:
    """publish_to_instagram is called and ig_url is written to the DB."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
        wp_post_id=42,
    )
    outcome = w._do_one(repo, repo.get_recipe(rid))

    assert outcome == "ig_post"
    assert len(patched["publish_to_instagram"]) == 1
    assert len(patched["html_to_png"]) == 1
    assert len(patched["upload_image_to_wp"]) == 1

    row = repo.get_recipe(rid)
    assert row is not None
    assert row.ig_url == "https://www.instagram.com/p/fake999/"
    assert row.publish_status["ig"]["state"] == "published"
    assert row.publish_status["ig"]["ref"] == "ig_media_999"


def test_do_one_dry_run_no_publish(tmp_path: Path, patched: dict[str, list]) -> None:
    """In dry-run mode targets are logged but publish is NOT called."""
    conn, repo = _repo(tmp_path)
    _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
        wp_post_id=42,
    )

    result = w.main(argv=[])  # no --apply → dry-run
    assert result == 0
    assert patched["publish_to_instagram"] == []


def test_do_one_idempotent(tmp_path: Path, patched: dict[str, list]) -> None:
    """Re-running on a recipe that already has ig_url set: primary query skips it."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
        wp_post_id=42,
    )
    # First run publishes it.
    w._do_one(repo, repo.get_recipe(rid))

    # After success the row no longer matches the primary predicate.
    primary_hits = [r for r in w._targets(repo, [], 0) if not r.ig_url]
    assert primary_hits == []
    # publish was called exactly once.
    assert len(patched["publish_to_instagram"]) == 1


def test_do_one_ig_exists(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _find_existing_ig_post returns a permalink, worker saves it without calling publish."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
        wp_post_id=42,
    )

    existing_url = "https://www.instagram.com/p/existing123/"

    monkeypatch.setattr(
        w,
        "_find_existing_ig_post",
        lambda recipe_name, wp_url: existing_url,
    )

    outcome = w._do_one(repo, repo.get_recipe(rid))

    assert outcome == "ig_exists"
    assert patched["publish_to_instagram"] == []

    row = repo.get_recipe(rid)
    assert row is not None
    assert row.ig_url == existing_url
    assert row.publish_status["ig"]["state"] == "published"
    assert row.publish_status["ig"]["url"] == existing_url
    assert row.publish_status["ig"]["ref"] == ""


def test_do_one_missing_html_raises(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    """_do_one raises FileNotFoundError when post_image_card.html is absent."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
    )

    # Override campaign_folder to return a dir WITHOUT the HTML file.
    def _empty_folder(row: RecipeRow) -> Path:
        folder = tmp_path / "empty" / row.id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    monkeypatch.setattr(w, "campaign_folder", _empty_folder)

    with pytest.raises(FileNotFoundError):
        w._do_one(repo, repo.get_recipe(rid))


# ----------------------------------------------------------------- captions


def test_do_one_caption_from_generated_content(tmp_path: Path, patched: dict[str, list]) -> None:
    """Caption from generated_content['ig_caption'] is passed to publish_to_instagram."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
        generated_content={"ig_caption": "My test caption"},
    )

    published_recipes: list[object] = []

    def _capture_publish(recipe: object, *, image_url: str) -> _FakeIGResult:
        published_recipes.append(recipe)
        return _FakeIGResult()

    import unittest.mock as mock

    with mock.patch.object(w, "publish_to_instagram", side_effect=_capture_publish):
        w._do_one(repo, repo.get_recipe(rid))

    assert len(published_recipes) == 1
    assert getattr(published_recipes[0], "ig_caption", None) == "My test caption"


def test_do_one_caption_from_file(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caption is read from ig_caption.txt when generated_content has no ig_caption."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
    )

    caption_text = "Caption from file"

    def _folder_with_caption(row: RecipeRow) -> Path:
        folder = tmp_path / "ready_cap" / row.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "post_image_card.html").write_text("<html><body></body></html>", encoding="utf-8")
        (folder / "ig_caption.txt").write_text(caption_text, encoding="utf-8")
        return folder

    monkeypatch.setattr(w, "campaign_folder", _folder_with_caption)

    published_recipes: list[object] = []

    import unittest.mock as mock

    with mock.patch.object(
        w,
        "publish_to_instagram",
        side_effect=lambda recipe, *, image_url: (
            published_recipes.append(recipe) or _FakeIGResult()
        ),
    ):
        w._do_one(repo, repo.get_recipe(rid))

    assert len(published_recipes) == 1
    assert getattr(published_recipes[0], "ig_caption", None) == caption_text


def test_do_one_caption_template_fallback(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Template caption is used when neither generated_content nor ig_caption.txt exist."""
    conn, repo = _repo(tmp_path)
    rid = _seed_row(
        repo,
        conn,
        "Pumpkin Treats",
        card_html_created_at="2026-01-01T00:00:00",
        wp_url="https://dogfoodandfun.com/pumpkin",
    )

    def _folder_no_caption(row: RecipeRow) -> Path:
        folder = tmp_path / "ready_tmpl" / row.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "post_image_card.html").write_text("<html><body></body></html>", encoding="utf-8")
        # No ig_caption.txt — forces template fallback
        return folder

    monkeypatch.setattr(w, "campaign_folder", _folder_no_caption)

    published_recipes: list[object] = []

    import unittest.mock as mock

    with mock.patch.object(
        w,
        "publish_to_instagram",
        side_effect=lambda recipe, *, image_url: (
            published_recipes.append(recipe) or _FakeIGResult()
        ),
    ):
        w._do_one(repo, repo.get_recipe(rid))

    assert len(published_recipes) == 1
    caption = getattr(published_recipes[0], "ig_caption", "") or ""
    # Template must include Nalla and at least one hashtag
    assert "Nalla" in caption
    assert "#" in caption
