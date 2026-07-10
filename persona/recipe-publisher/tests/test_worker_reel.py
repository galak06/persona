"""Tests for Worker C (reel): the DB-polling predicate, the compose-from-reel_src
task, idempotency, and the missing-frames gate. Real temp sqlite DB; ffmpeg
(compose_reel) is monkeypatched so no video is actually encoded.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers import worker_reel as w


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _row_with_slides(repo: RecipeRepository, name: str, *, slides: bool) -> str:
    row = RecipeRow(name=name, dog_safe=True, content_hash=name)
    repo.upsert_recipe(row)
    rid = row.ensure_id()
    if slides:
        repo.set_slides(rid, 2, "2026-06-14T00:00:00Z")
    return rid


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Patch compose_reel + campaign_folder; record the frames it received."""
    calls: dict[str, list] = {"compose": []}

    def _fake_compose(frames, output_path, *, audio_path=None):
        calls["compose"].append((list(frames), Path(output_path), audio_path))
        Path(output_path).write_bytes(b"MP4")
        return Path(output_path)

    monkeypatch.setattr("generators.reel.compose_reel", _fake_compose)
    monkeypatch.setattr(w, "campaign_folder", lambda row: tmp_path / "ready" / row.id)
    return calls


def _seed_reel_src(tmp_path: Path, rid: str, n: int = 2) -> None:
    src = tmp_path / "ready" / rid / "reel_src"
    src.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (src / f"slide_{i}.jpg").write_bytes(f"reel{i}".encode())


# ----------------------------------------------------------------- predicate
def test_targets_needs_slides_without_reel(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    ready = _row_with_slides(repo, "Beef Bowl", slides=True)
    _row_with_slides(repo, "No Slides", slides=False)  # excluded
    assert [r.id for r in w._targets(repo, [], 0)] == [ready]


def test_targets_excludes_done(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    rid = _row_with_slides(repo, "Beef Bowl", slides=True)
    repo.set_reel(rid, "2026-06-14T01:00:00Z")
    assert w._targets(repo, [], 0) == []


# --------------------------------------------------------------------- task
def test_do_one_composes_from_reel_src(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    _, repo = _repo(tmp_path)
    rid = _row_with_slides(repo, "Beef Bowl", slides=True)
    _seed_reel_src(tmp_path, rid, n=2)

    assert w._do_one(repo, repo.get_recipe(rid)) == "reel"

    frames, out, audio = patched["compose"][0]
    assert frames == [b"reel1", b"reel2"]  # un-badged frames, ordered
    assert out.name == "source.mp4" and out.read_bytes() == b"MP4"
    assert audio is None  # silent reel
    assert repo.get_recipe(rid).reel_created_at  # marked done


def test_do_one_is_idempotent(tmp_path: Path, patched: dict[str, list]) -> None:
    _, repo = _repo(tmp_path)
    rid = _row_with_slides(repo, "Beef Bowl", slides=True)
    _seed_reel_src(tmp_path, rid)
    w._do_one(repo, repo.get_recipe(rid))
    assert w._targets(repo, [], 0) == []  # not re-selected after success


def test_do_one_no_frames_leaves_gate_open(
    tmp_path: Path, patched: dict[str, list]
) -> None:
    _, repo = _repo(tmp_path)
    rid = _row_with_slides(repo, "Beef Bowl", slides=True)
    # No reel_src/ on disk.
    assert w._do_one(repo, repo.get_recipe(rid)) == "no-frames"
    assert repo.get_recipe(rid).reel_created_at == ""  # still selectable
    assert patched["compose"] == []  # compose never called
