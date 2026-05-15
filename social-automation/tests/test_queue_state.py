# pyright: reportMissingImports=false
# ruff: noqa: S101
"""Tests for lib/queue_state.py — the producer-facing queue wrapper.

The wrapper sits on top of ``api/state.py``'s flock + atomic-write contract;
these tests focus on the Phase-3 additions (write_pending stamping,
idempotency, telegram-channel commits) and on the concurrency guarantees we
inherit from the underlying layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from queue_state import (  # noqa: E402
    commit_telegram_decision,
    read_decision,
    write_pending,
)


def _read_raw(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_pending_stamps_required_fields(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    item = {"platform": "facebook", "post_id": "abc123", "draft_comment": "hi"}
    item_id = write_pending(qpath, item)
    stored = _read_raw(qpath)
    assert len(stored) == 1
    row = stored[0]
    assert row["id"] == item_id
    assert row["status"] == "pending"
    assert row["decided_by"] is None
    assert row["decided_at"] is None
    assert isinstance(row["created_at"], str) and row["created_at"]


def test_write_pending_is_idempotent(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    item = {"platform": "instagram", "post_id": "ig-xyz"}
    first = write_pending(qpath, item)
    second = write_pending(qpath, item)
    assert first == second
    assert len(_read_raw(qpath)) == 1


def test_read_decision_returns_none_for_missing(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    qpath.write_text("[]", encoding="utf-8")
    assert read_decision(qpath, "nope") is None


def test_commit_telegram_decision_writes_decided_by(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    item_id = write_pending(qpath, {"platform": "facebook", "post_id": "p1"})
    result = commit_telegram_decision(
        qpath,
        item_id,
        status="approved",
        text="approved comment text",
    )
    assert result == "committed"
    row = read_decision(qpath, item_id)
    assert row is not None
    assert row["decided_by"] == "telegram"
    assert row["status"] == "approved"
    assert row["comment_text"] == "approved comment text"


def test_commit_decision_409_on_already_decided(tmp_path: Path) -> None:
    qpath = tmp_path / "queue.json"
    item_id = write_pending(qpath, {"platform": "facebook", "post_id": "p2"})
    first = commit_telegram_decision(qpath, item_id, status="approved")
    second = commit_telegram_decision(qpath, item_id, status="USER_SKIPPED")
    assert first == "committed"
    assert second == "already_decided"


def _race_worker(
    qpath_str: str,
    item_id: str,
    status: str,
    out_queue: Any,
    ready: Any,
    go: Any,
) -> None:
    """Module-level so multiprocessing.Process can pickle it. ``go.wait()``
    plus ``ready.set()`` reproduces a Barrier across processes."""
    import sys as _sys
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(_here))
    _sys.path.insert(0, str(_here / "lib"))
    from queue_state import (  # noqa: E402 — sys.path tweak in subprocess
        commit_telegram_decision as _commit,
    )

    ready.set()
    go.wait()
    res = _commit(_Path(qpath_str), item_id, status=status)
    out_queue.put(res)


def test_concurrent_write_safety(tmp_path: Path) -> None:
    """Two PROCESSES race on the same item; exactly one should win.

    Uses multiprocessing because ``fcntl.flock`` is a per-process lock — two
    threads in the same interpreter both see the lock as theirs, which would
    give false reassurance. We want to prove the cross-process contract that
    matches the real Telegram + FastAPI deployment.
    """
    import multiprocessing as mp

    qpath = tmp_path / "queue.json"
    item_id = write_pending(qpath, {"platform": "facebook", "post_id": "race"})

    ctx = mp.get_context("spawn")
    out_queue: Any = ctx.Queue()
    ready1 = ctx.Event()
    ready2 = ctx.Event()
    go = ctx.Event()

    p1 = ctx.Process(
        target=_race_worker,
        args=(str(qpath), item_id, "approved", out_queue, ready1, go),
    )
    p2 = ctx.Process(
        target=_race_worker,
        args=(str(qpath), item_id, "USER_SKIPPED", out_queue, ready2, go),
    )
    p1.start()
    p2.start()
    assert ready1.wait(timeout=10)
    assert ready2.wait(timeout=10)
    go.set()
    p1.join(timeout=10)
    p2.join(timeout=10)

    results = sorted([out_queue.get(timeout=5), out_queue.get(timeout=5)])
    assert results == ["already_decided", "committed"]


def test_atomic_write_rollback_on_error(tmp_path: Path) -> None:
    """If os.replace fails mid-write, the queue file must keep its old content."""
    qpath = tmp_path / "queue.json"
    item_id = write_pending(qpath, {"platform": "facebook", "post_id": "rollback"})
    original = qpath.read_text(encoding="utf-8")

    with mock.patch("api.state.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            commit_telegram_decision(qpath, item_id, status="approved")

    # The original on-disk content survives the failed commit.
    assert qpath.read_text(encoding="utf-8") == original
    row = read_decision(qpath, item_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["decided_by"] is None
