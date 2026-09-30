"""Tests for `gate_factory.build_post_gate`, `gate_budget.RunBudget` and `shadow_worker`."""

from __future__ import annotations

import threading
import time

import pytest

from lib.decisions import gate_factory, jev_client
from lib.decisions.gate_budget import DRAIN_TIMEOUT_S, RunBudget, drain_timeout
from lib.decisions.gate_factory import build_post_gate
from lib.decisions.shadow_worker import ShadowWorker


def test_build_gate_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jev_client.API_KEY_ENV, raising=False)
    monkeypatch.setattr(jev_client, "_missing_key_warned", True)
    assert build_post_gate("instagram", "ig-engager") is None


@pytest.mark.parametrize(
    ("row", "platform", "expected"),
    [
        ({"jev_post_gate_ig": "shadow", "focus_category": "Dog Food"}, "instagram", "shadow"),
        ({"jev_post_gate_fb": "enforce", "niche": "dogs"}, "facebook", "enforce"),
        ({"jev_post_gate_ig": "off"}, "instagram", None),
        ({"niche": "dogs"}, "instagram", None),  # DB predates the columns -> off
        ({"jev_post_gate_ig": "shadow"}, "tiktok", None),
    ],
)
def test_build_gate_reads_mode_from_brand_row(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, str], platform: str, expected: str | None
) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, "test-not-a-real-key")
    monkeypatch.setattr(gate_factory, "current_brand_id", lambda: "acme")
    monkeypatch.setattr(gate_factory.brands_db, "get", lambda _bid: row)
    gate = build_post_gate(platform, "flow")
    assert (gate.mode if gate else None) == expected
    if gate is not None:
        assert gate.brand_focus == (row.get("focus_category") or row.get("niche"))


def test_unregistered_brand_warns_with_brand_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, "test-not-a-real-key")
    monkeypatch.setenv("BRAND_DIR", "/brands/ghost")
    monkeypatch.setattr(gate_factory, "current_brand_id", lambda: "ghost")
    monkeypatch.setattr(gate_factory.brands_db, "get", lambda _bid: None)
    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(gate_factory.log, "warning", lambda e, **kw: warnings.append((e, kw)))
    assert build_post_gate("instagram", "ig-engager") is None
    event, fields = warnings[0]
    assert event == "jev_gate_brand_unregistered"
    assert (fields["brand_id"], fields["brand_dir"]) == ("ghost", "/brands/ghost")


def test_build_gate_survives_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, "test-not-a-real-key")

    def _boom(_bid: str) -> None:
        raise RuntimeError("no db")

    monkeypatch.setattr(gate_factory.brands_db, "get", _boom)
    assert build_post_gate("instagram", "ig-engager") is None


# --- RunBudget ---------------------------------------------------------------


def test_budget_breaker_resets_on_success() -> None:
    b = RunBudget(platform="instagram", clock=lambda: 0.0)
    for ok in (False, True, False):
        assert b.allow()
        b.finish(b.start(), ok=ok)
    assert b.allow()  # never two failures IN A ROW
    b.finish(b.start(), ok=False)
    assert not b.allow()
    assert b.tripped == "circuit_breaker"
    assert b.failures == 3


def test_budget_call_cap_and_trip_is_sticky() -> None:
    b = RunBudget(platform="facebook", max_calls=1, clock=lambda: 0.0)
    b.finish(b.start(), ok=True)
    assert not b.allow()
    assert b.tripped == "call_cap"
    b.max_calls = 10
    assert not b.allow()  # stays off for the rest of the run


# --- ShadowWorker / drain_timeout ---------------------------------------------


def test_drain_timeout_is_clamped_to_the_deadline() -> None:
    assert drain_timeout(None) == DRAIN_TIMEOUT_S
    assert drain_timeout(3.5) == 3.5
    assert drain_timeout(1e6) == DRAIN_TIMEOUT_S
    assert drain_timeout(-5.0) == 0.0  # deadline passed: no wait at all


def test_worker_runs_fifo_and_drains() -> None:
    worker = ShadowWorker("jev-gate-test")
    seen: list[int] = []
    for i in range(5):
        worker.submit(lambda i=i: seen.append(i))
    assert worker.drain(2.0) == 0
    assert seen == [0, 1, 2, 3, 4]
    worker.submit(lambda: seen.append(99))  # after drain: ignored
    time.sleep(0.05)
    assert seen == [0, 1, 2, 3, 4]


def test_worker_close_discards_queued_and_reports_them() -> None:
    worker = ShadowWorker("jev-gate-test")
    release = threading.Event()
    ran: list[int] = []
    worker.submit(release.wait)  # blocks the single worker
    for i in range(3):
        worker.submit(lambda i=i: ran.append(i))
    started = time.monotonic()
    undrained = worker.drain(0.05)
    assert time.monotonic() - started < 1.0
    assert undrained == 4  # the running task + 3 queued
    release.set()
    time.sleep(0.1)
    assert ran == []  # queued work was discarded, never run


def test_worker_survives_a_failing_task() -> None:
    worker = ShadowWorker("jev-gate-test")
    ran: list[str] = []

    def _boom() -> None:
        raise RuntimeError("task bug")

    worker.submit(_boom)
    worker.submit(lambda: ran.append("after"))
    assert worker.drain(2.0) == 0
    assert ran == ["after"]
