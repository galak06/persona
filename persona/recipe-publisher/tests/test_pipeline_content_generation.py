# pyright: reportMissingImports=false
"""Tests for the content-generation phase (injected producer; no API calls)."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline.checkpoint import CheckpointError
from pipeline.content_generation import ContentGenerator
from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository


class _FakeProducer:
    def produce(self, row: RecipeRow) -> dict[str, str]:
        return {
            "title": f"Draft {row.name}",
            "body_markdown": "Body.",
            "ig_caption": "Caption.",
            "image_brief": "Brief.",
        }


class _IncompleteProducer:
    def produce(self, row: RecipeRow) -> dict[str, str]:
        return {"title": "T", "body_markdown": "", "ig_caption": "C"}


def _seed(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    repo = RecipeRepository(conn)
    repo.upsert_recipe(RecipeRow(name="Safe One", dog_safe=True, content_hash="s1"))
    repo.upsert_recipe(RecipeRow(name="Safe Two", dog_safe=True, content_hash="s2"))
    repo.upsert_recipe(RecipeRow(name="Unsafe", dog_safe=False, content_hash="u1"))
    return conn, repo


def test_generates_for_eligible_only(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = ContentGenerator(repo, _FakeProducer()).run(persist=True)
        assert report.eligible == 2  # unsafe excluded
        assert report.generated == 2
        row = repo.get_recipe("safe-one")
        assert row is not None
        assert row.content_status == ContentStatus.GENERATED
        assert row.generated_content["title"] == "Draft Safe One"
    finally:
        conn.close()


def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = ContentGenerator(repo, _FakeProducer()).run(persist=False)
        assert report.generated == 0
        row = repo.get_recipe("safe-one")
        assert row is not None
        assert row.content_status == ContentStatus.NONE
    finally:
        conn.close()


def test_gate_fails_on_incomplete_draft(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        with pytest.raises(CheckpointError):
            ContentGenerator(repo, _IncompleteProducer()).run(persist=True)
    finally:
        conn.close()


def test_limit_caps_generation(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = ContentGenerator(repo, _FakeProducer()).run(persist=True, limit=1)
        assert report.generated == 1
    finally:
        conn.close()
