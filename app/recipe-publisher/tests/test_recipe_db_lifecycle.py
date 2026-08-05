# pyright: reportMissingImports=false
"""Lifecycle-column round-trip tests for recipe_db (generated_content,
content_status, publish_results). No network."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def test_lifecycle_defaults(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Plain", content_hash="a"))
        row = repo.get_recipe("plain")
        assert row is not None
        assert row.generated_content == {}
        assert row.content_status == ContentStatus.NONE
        assert row.publish_results == []
    finally:
        conn.close()


def test_set_generated_content_advances_status(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    payload = {"title": "T", "body_markdown": "B", "ig_caption": "C"}
    try:
        repo.upsert_recipe(RecipeRow(name="Gen", content_hash="b"))
        repo.set_generated_content("gen", payload, ContentStatus.GENERATED)
        row = repo.get_recipe("gen")
        assert row is not None
        assert row.generated_content == payload
        assert row.content_status == ContentStatus.GENERATED
    finally:
        conn.close()


def test_set_content_status_and_list_by_status(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="A", content_hash="c1"))
        repo.upsert_recipe(RecipeRow(name="B", content_hash="c2"))
        repo.set_content_status("a", ContentStatus.PENDING)
        pending = repo.list_by_content_status(ContentStatus.PENDING)
        assert [r.id for r in pending] == ["a"]
    finally:
        conn.close()


def test_set_publish_results_round_trip(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    results = [{"platform": "ig", "status": "published", "ref": "123"}]
    try:
        repo.upsert_recipe(RecipeRow(name="Pub", content_hash="d"))
        repo.set_publish_results("pub", results)
        row = repo.get_recipe("pub")
        assert row is not None
        assert row.publish_results == results
    finally:
        conn.close()
