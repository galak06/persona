# pyright: reportMissingImports=false
"""Tests for the pending-review (phase 4) and approval (phase 5) phases."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline.approval import ApprovalError, ApprovalService
from pipeline.pending_review import ReviewStager
from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

_COMPLETE = {"title": "T", "body_markdown": "B", "ig_caption": "C"}
_INCOMPLETE = {"title": "T", "body_markdown": ""}


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def test_review_stages_only_complete(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Good", content_hash="g"))
        repo.upsert_recipe(RecipeRow(name="Bad", content_hash="b"))
        repo.set_generated_content("good", _COMPLETE, ContentStatus.GENERATED)
        repo.set_generated_content("bad", _INCOMPLETE, ContentStatus.GENERATED)
        report = ReviewStager(repo).run(persist=True)
        assert report.staged == 1
        assert report.incomplete == 1
        good = repo.get_recipe("good")
        bad = repo.get_recipe("bad")
        assert good is not None and good.content_status == ContentStatus.PENDING
        assert bad is not None and bad.content_status == ContentStatus.GENERATED
    finally:
        conn.close()


def test_approve_pending(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="P", content_hash="p"))
        repo.set_content_status("p", ContentStatus.PENDING)
        ApprovalService(repo).approve("p")
        row = repo.get_recipe("p")
        assert row is not None and row.content_status == ContentStatus.APPROVED
    finally:
        conn.close()


def test_reject_pending(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="P", content_hash="p"))
        repo.set_content_status("p", ContentStatus.PENDING)
        ApprovalService(repo).reject("p")
        row = repo.get_recipe("p")
        assert row is not None and row.content_status == ContentStatus.REJECTED
    finally:
        conn.close()


def test_approve_non_pending_raises(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="G", content_hash="g"))
        repo.set_content_status("g", ContentStatus.GENERATED)
        with pytest.raises(ApprovalError):
            ApprovalService(repo).approve("g")
    finally:
        conn.close()


def test_approve_missing_raises(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        with pytest.raises(ApprovalError):
            ApprovalService(repo).approve("nope")
    finally:
        conn.close()
