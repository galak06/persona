"""Tests for the drafting claim's lease, as seen by the cron safety-net sweep
(`recipe-publisher/workers/worker_wp_ideas.py`).

The bug these pin: both drafting callers release their claim IN-PROCESS, so a
holder that is killed outright (container restart, OOM, deploy) leaves the idea
parked at status='drafting' forever -- and a sweep whose candidate query asks
only for status='approved' can never see the row it left behind. Live on
2026-08-31: idea f4df76d6 was claimed at 12:05:02Z and orphaned 71 seconds
later by an API container restart, with the Ideas page still showing
"Drafting".

The worker lives under `recipe-publisher/`, whose hyphen makes it un-importable
as a package (it runs as `python -m workers.worker_wp_ideas` with cwd set into
that directory), so it is loaded here by file path.
"""
# ruff: noqa: S101

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lib import ideas_db

_WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "recipe-publisher"
    / "workers"
    / "worker_wp_ideas.py"
)


def _load_worker() -> Any:
    spec = importlib.util.spec_from_file_location("_worker_wp_ideas_under_test", _WORKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker() -> Any:
    return _load_worker()


def _idea(idea_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"id": idea_id, "status": status, "wp_url": None, "topic": "t", **extra}


# ─────────────────────────────────────────────────────────────────────────────
# _targets -- the sweep must SELECT stranded ideas, not just be able to claim them


def test_targets_includes_ideas_stranded_under_an_expired_lease(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker.ideas_db, "list_ideas", lambda **_: [_idea("approved-1", "approved")]
    )
    monkeypatch.setattr(
        worker.ideas_db, "list_stale_drafting", lambda **_: [_idea("stranded-1", "drafting")]
    )

    ids = [r["id"] for r in worker._targets(brand_id="acme", limit=5, idea_id=None)]

    assert ids == ["approved-1", "stranded-1"]


def test_targets_asks_for_stale_ideas_scoped_to_this_brand(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep for one brand must never reclaim another brand's ideas."""
    seen: dict[str, Any] = {}

    def _stale(**kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(worker.ideas_db, "list_ideas", lambda **_: [])
    monkeypatch.setattr(worker.ideas_db, "list_stale_drafting", _stale)

    worker._targets(brand_id="acme", limit=5, idea_id=None)

    assert seen["brand_id"] == "acme"


def test_targets_still_excludes_anything_already_written_to_wordpress(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker.ideas_db, "list_ideas", lambda **_: [])
    monkeypatch.setattr(
        worker.ideas_db,
        "list_stale_drafting",
        lambda **_: [_idea("done-1", "drafting", wp_url="https://example.com/?p=1")],
    )

    assert worker._targets(brand_id="acme", limit=5, idea_id=None) == []


def test_targets_caps_the_union_so_limit_still_means_what_it_says(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sources are limited separately, so the union can otherwise be 2x."""
    monkeypatch.setattr(
        worker.ideas_db, "list_ideas", lambda **_: [_idea(f"a-{i}", "approved") for i in range(3)]
    )
    monkeypatch.setattr(
        worker.ideas_db,
        "list_stale_drafting",
        lambda **_: [_idea(f"s-{i}", "drafting") for i in range(3)],
    )

    assert len(worker._targets(brand_id="acme", limit=3, idea_id=None)) == 3


# ─────────────────────────────────────────────────────────────────────────────
# _do_one -- a reclaim is a distinct, greppable event


def _stub_subprocess(worker: Any, monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> dict:
    captured: dict[str, Any] = {}

    def _run(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(worker.subprocess, "run", _run)
    return captured


def test_reclaiming_a_stranded_idea_is_logged_distinctly(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(worker.ideas_db, "claim_idea_for_drafting", lambda _: True)
    _stub_subprocess(worker, monkeypatch)

    with caplog.at_level(logging.INFO):
        outcome = worker._do_one(_idea("stranded-1", "drafting"), dry_run=False)

    events = [json.loads(r.message)["event"] for r in caplog.records if r.message.startswith("{")]
    assert outcome == "drafted"
    assert "idea_draft_claim_reclaimed" in events


def test_a_normal_approved_idea_is_not_reported_as_reclaimed(
    worker: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(worker.ideas_db, "claim_idea_for_drafting", lambda _: True)
    _stub_subprocess(worker, monkeypatch)

    with caplog.at_level(logging.INFO):
        worker._do_one(_idea("approved-1", "approved"), dry_run=False)

    events = [json.loads(r.message)["event"] for r in caplog.records if r.message.startswith("{")]
    assert "idea_draft_claim_reclaimed" not in events


def test_a_live_lease_still_blocks_a_second_drafter(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease must not weaken the concurrency guard: a refused claim still
    means no subprocess is spawned."""
    monkeypatch.setattr(worker.ideas_db, "claim_idea_for_drafting", lambda _: False)

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a refused claim must never spawn a drafting subprocess")

    monkeypatch.setattr(worker.subprocess, "run", _explode)

    assert worker._do_one(_idea("held-1", "drafting"), dry_run=False) == "skipped"


def test_drafting_subprocess_timeout_stays_shorter_than_the_lease(
    worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift here is the failure mode the shared constants exist to prevent: a
    timeout longer than the lease means a healthy draft gets drafted twice."""
    monkeypatch.setattr(worker.ideas_db, "claim_idea_for_drafting", lambda _: True)
    captured = _stub_subprocess(worker, monkeypatch)

    worker._do_one(_idea("approved-1", "approved"), dry_run=False)

    timeout = captured["kwargs"]["timeout"]
    assert timeout == ideas_db.DRAFT_SUBPROCESS_TIMEOUT_SECONDS
    assert timeout < ideas_db.DRAFT_CLAIM_LEASE_SECONDS
