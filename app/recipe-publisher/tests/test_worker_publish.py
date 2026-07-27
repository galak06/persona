"""Tests for Worker D (publish): audio detection, the DB-polling predicate, the
publish task, idempotency, and failure handling. The heavy folder-driven
publisher (scripts.publish_prepared) is replaced via sys.modules with a fake, so
no WP/IG/FB calls happen and its config import is never triggered.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_publish as w

_TS = "2026-06-14T00:00:00Z"


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _row(repo: RecipeRepository, name: str, *, reel: bool, audio: bool) -> str:
    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    repo.upsert_recipe(row)
    rid = row.ensure_id()
    if reel:
        repo.set_slides(rid, 2, _TS)
        repo.set_reel(rid, _TS)
    if audio:
        repo.set_audio_ready(rid, _TS)
    return rid


@pytest.fixture
def fake_pp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Inject a fake scripts.publish_prepared and point campaign_folder at tmp."""
    state: dict[str, object] = {
        "publish_calls": [],
        "audio_present": set(),  # recipe ids whose folder "has" audio
        "publish_returns": True,
    }
    mod = types.ModuleType("scripts.publish_prepared")

    def _publish_one(folder: Path, *, dry_run: bool, skip_pdf: bool = False) -> bool:
        state["publish_calls"].append((Path(folder), dry_run, skip_pdf))  # type: ignore[attr-defined]
        return bool(state["publish_returns"])

    def _resolve_audio_path(folder: Path) -> Path | None:
        rid = Path(folder).name
        return (Path(folder) / "audio.mp3") if rid in state["audio_present"] else None  # type: ignore[operator]

    mod.publish_one = _publish_one  # type: ignore[attr-defined]
    mod._resolve_audio_path = _resolve_audio_path  # type: ignore[attr-defined]
    mod._read_metadata = lambda folder: {  # type: ignore[attr-defined]
        "ig_reel_permalink": "https://ig/reel/1",
        "fb_reel_permalink": "https://fb/reel/1",
    }
    monkeypatch.setitem(sys.modules, "scripts.publish_prepared", mod)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    return state


# ----------------------------------------------------------------- audio gate
def test_detect_audio_sets_marker(tmp_path: Path, fake_pp: dict) -> None:
    _, repo = _repo(tmp_path)
    rid = _row(repo, "Beef Bowl", reel=True, audio=False)
    fake_pp["audio_present"].add(rid)  # operator dropped audio.mp3

    w._detect_audio(repo)
    assert repo.get_recipe(rid).audio_ready_at  # marker now set


def test_detect_audio_skips_when_absent(tmp_path: Path, fake_pp: dict) -> None:
    _, repo = _repo(tmp_path)
    rid = _row(repo, "Beef Bowl", reel=True, audio=False)
    w._detect_audio(repo)
    assert repo.get_recipe(rid).audio_ready_at == ""  # no audio → still gated


# ----------------------------------------------------------------- predicate
def test_targets_needs_reel_and_audio(tmp_path: Path, fake_pp: dict) -> None:
    _, repo = _repo(tmp_path)
    ready = _row(repo, "Ready One", reel=True, audio=True)
    _row(repo, "No Audio", reel=True, audio=False)  # excluded
    _row(repo, "No Reel", reel=False, audio=True)  # excluded
    assert [r.id for r in w._targets(repo, [], 0)] == [ready]


# --------------------------------------------------------------------- task
def test_do_one_publishes_skip_pdf_and_marks(tmp_path: Path, fake_pp: dict) -> None:
    _, repo = _repo(tmp_path)
    rid = _row(repo, "Beef Bowl", reel=True, audio=True)

    assert w._do_one(repo, repo.get_recipe(rid)) == "published"
    _folder, dry_run, skip_pdf = fake_pp["publish_calls"][0]
    assert dry_run is False and skip_pdf is True  # Worker A owns the PDF
    row = repo.get_recipe(rid)
    assert row.social_published_at  # marker recorded
    assert row.ig_url == "https://ig/reel/1"  # badges synced from metadata
    assert row.fb_url == "https://fb/reel/1"


def test_do_one_is_idempotent(tmp_path: Path, fake_pp: dict) -> None:
    _, repo = _repo(tmp_path)
    rid = _row(repo, "Beef Bowl", reel=True, audio=True)
    w._do_one(repo, repo.get_recipe(rid))
    assert w._targets(repo, [], 0) == []  # not re-selected after success


def test_do_one_publish_failure_leaves_gate_open(
    tmp_path: Path, fake_pp: dict
) -> None:
    _, repo = _repo(tmp_path)
    rid = _row(repo, "Beef Bowl", reel=True, audio=True)
    fake_pp["publish_returns"] = False  # publisher reports failure

    assert w._do_one(repo, repo.get_recipe(rid)) == "publish-failed"
    assert repo.get_recipe(rid).social_published_at == ""  # still selectable
