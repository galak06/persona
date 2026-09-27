"""Tests for `lib.decisions.gate_factory.build_post_gate` and `gate_budget.RunBudget`."""

from __future__ import annotations

import pytest

from lib.decisions import gate_factory, jev_client
from lib.decisions.gate_budget import RunBudget
from lib.decisions.gate_factory import build_post_gate


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
