"""Tests for Worker A (wp+pdf): the DB-polling predicate, the WP+PDF task, the
PDF self-heal, and idempotency. Uses a real temp sqlite DB (the predicates ARE
the product) with the heavy collaborators (WP / image / PDF) monkeypatched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_wp_pdf as w


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _seed_row(repo: RecipeRepository, name: str, *, dog_safe: bool = True) -> str:
    # Distinct content_hash per row — the table enforces UNIQUE(content_hash).
    row = RecipeRow(name=name, dog_safe=dog_safe, content_hash=name)
    repo.upsert_recipe(row)
    return row.ensure_id()


@dataclass
class _FakeRecipe:
    slug: str
    title: str
    image_brief: str
    seed_id: str = ""
    ig_caption: str = "Nalla approves this recipe! 🐾"
    tags: list[str] = field(default_factory=lambda: ["dog-treats"])


@dataclass
class _FakeWP:
    post_id: int
    permalink: str
    featured_image_url: str = ""


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Patch Worker A's collaborators with deterministic fakes; record calls."""
    calls: dict[str, list] = {"wp": [], "pdf": [], "rehydrate": []}

    def _rehydrate(row: RecipeRow) -> _FakeRecipe:
        calls["rehydrate"].append(row.id)
        return _FakeRecipe(
            slug=row.id, title=row.name, image_brief="brief", seed_id=row.id
        )

    monkeypatch.setattr(w, "ensure_seed_exported", lambda row: None)
    monkeypatch.setattr(w, "rehydrate_recipe", _rehydrate)
    monkeypatch.setattr(w, "_wp_live_post", lambda slug: None)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    monkeypatch.setattr(
        "generators.image.generate_image",
        lambda brief, *, alt_hint: object(),
    )

    def _fake_wp(recipe: object, image: object) -> _FakeWP:
        calls["wp"].append(getattr(recipe, "slug", "?"))
        return _FakeWP(post_id=123, permalink="https://x/p/123")

    monkeypatch.setattr("publishers.wordpress.publish_to_wordpress", _fake_wp)

    def _fake_pdf(wp_id: int) -> str:
        calls["pdf"].append(wp_id)
        return "https://x/card-123.pdf"

    monkeypatch.setattr(w, "_generate_pdf", _fake_pdf)
    return calls


# ----------------------------------------------------------------- predicate
def test_targets_selects_dog_safe_without_wp(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    safe = _seed_row(repo, "Beef Bowl", dog_safe=True)
    _seed_row(repo, "Onion Stew", dog_safe=False)  # unsafe → excluded
    ids = [r.id for r in w._targets(repo, [], 0)]
    assert ids == [safe]


def test_targets_selects_pdf_self_heal(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    # Simulate "WP done, PDF missing": wp_url + wp_post_id set, pdf_url empty.
    repo.set_publish_status(rid, {"wp": {"url": "https://x/p", "state": "published"}})
    repo.set_wp_post_id(rid, 123)
    assert [r.id for r in w._targets(repo, [], 0)] == [rid]  # PDF arm re-selects


def test_targets_respects_seed_and_limit(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    a = _seed_row(repo, "Apple Crunch")
    _seed_row(repo, "Beef Bowl")
    assert [r.id for r in w._targets(repo, [a], 0)] == [a]
    assert len(w._targets(repo, [], 1)) == 1


# --------------------------------------------------------------------- task
def test_do_one_full_wp_and_pdf(tmp_path: Path, patched: dict[str, list]) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    outcome = w._do_one(repo, repo.get_recipe(rid))

    assert outcome == "wp+pdf"
    row = repo.get_recipe(rid)
    assert row is not None
    assert row.wp_url == "https://x/p/123"
    assert row.wp_post_id == 123
    assert row.pdf_url == "https://x/card-123.pdf"
    assert row.artifacts_path == f"campaigns/recipes/ready/{rid}"
    assert row.publish_status["wp"]["ref"] == "123"
    assert row.publish_status["pdf"]["url"] == "https://x/card-123.pdf"
    assert row.content_status == "none"  # dormant pipeline untouched


def test_do_one_writes_publish_inputs(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    """Worker A leaves the folder files Worker D (publish_one) will consume."""
    import json

    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    w._do_one(repo, repo.get_recipe(rid))

    folder = tmp_path / "ready" / rid
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["wp_draft_id"] == 123
    assert meta["slug"] == rid
    assert (folder / "ig_caption.txt").read_text(encoding="utf-8").startswith("Nalla")
    assert (folder / "fb_caption.txt").exists()


def test_do_one_is_idempotent(tmp_path: Path, patched: dict[str, list]) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    w._do_one(repo, repo.get_recipe(rid))
    # After success the row matches no predicate arm → not re-selected.
    assert w._targets(repo, [], 0) == []


def test_pdf_failure_leaves_pdf_gate_open(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    monkeypatch.setattr(w, "_generate_pdf", lambda wp_id: "")  # PDF fails

    assert w._do_one(repo, repo.get_recipe(rid)) == "wp+nopdf"
    row = repo.get_recipe(rid)
    assert row is not None and row.wp_url and not row.pdf_url
    # The PDF arm re-selects this row for a retry.
    assert [r.id for r in w._targets(repo, [], 0)] == [rid]


def test_pdf_self_heal_skips_wp(
    tmp_path: Path, patched: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repo = _repo(tmp_path)
    rid = _seed_row(repo, "Beef Bowl")
    repo.set_publish_status(rid, {"wp": {"url": "https://x/p", "state": "published"}})
    repo.set_wp_post_id(rid, 123)

    def _boom(row: RecipeRow) -> object:
        raise AssertionError("WP arm must not run on PDF self-heal")

    monkeypatch.setattr(w, "rehydrate_recipe", _boom)
    assert w._do_one(repo, repo.get_recipe(rid)) == "pdf"
    assert repo.get_recipe(rid).pdf_url == "https://x/card-123.pdf"
    assert patched["rehydrate"] == []  # WP arm skipped
