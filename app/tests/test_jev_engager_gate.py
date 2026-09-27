"""Unit tests for `lib.decisions.engager_gate.JevPostGate` (no network, no DB).

Shadow runs Jev on a background worker, so every shadow test calls
`gate.close()` (the bounded drain) before asserting on the store.
"""

from __future__ import annotations

import pytest

from lib import draft_helper
from lib.decisions import decisions_db, engager_gate
from lib.decisions.engager_gate import JevPostGate, outcome_for
from lib.decisions.modes import GateMode
from tests._jev_fakes import KEEP, SKIP_ALL, FakeJev, FakeStore, join_gate_threads, make_post


def _gate(mode: GateMode, **kw: object) -> JevPostGate:
    return JevPostGate(
        brand_id="b",
        flow="ig-engager",
        platform="instagram",
        mode=mode,
        brand_focus="dog food",
        **kw,  # type: ignore[arg-type]
    )


# --- shadow ------------------------------------------------------------------


def test_shadow_runs_off_the_scan_thread_and_never_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    jev = FakeJev(SKIP_ALL).install(monkeypatch)
    gate = _gate("shadow")

    assert gate.before_draft(make_post()) is False
    gate.close()

    assert jev.threads and all(t.startswith("jev-gate") for t in jev.threads)
    rec = store.decisions[0]
    assert (rec.mode, rec.would_skip, rec.item_key) == ("shadow", True, "https://x/p/p1")
    assert (rec.cost_usd, rec.latency_ms, rec.error) == (0.0001, 12, None)
    assert store.outcomes == []  # shadow never stamps skipped_by_gate


def test_shadow_outcome_survives_either_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drafter usually finishes BEFORE the slow Jev call lands."""
    store = FakeStore().install(monkeypatch)
    FakeJev(KEEP, sleep_s=0.05).install(monkeypatch)
    gate = _gate("shadow")

    post = make_post()
    gate.before_draft(post)
    gate.after_draft(post, drafted=False, reason=draft_helper.AGENT_DECLINED)
    gate.close()

    assert store.final_outcomes() == {"https://x/p/p1": "declined"}


def test_outcome_parked_before_row_is_folded_into_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce path, but the drafter outcome is already pending at insert time."""
    store = FakeStore().install(monkeypatch)
    FakeJev(KEEP).install(monkeypatch)
    gate = _gate("enforce")
    post = make_post()
    gate.after_draft(post, drafted=True, reason=None)  # no row yet: parked
    assert store.outcomes == []
    gate.before_draft(post)
    assert store.decisions[0].outcome == "engaged"


def test_shadow_submission_cap_bounds_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeStore().install(monkeypatch)
    jev = FakeJev(KEEP).install(monkeypatch)
    gate = _gate("shadow", max_calls=2)
    for i in range(5):
        gate.before_draft(make_post(f"p{i}"))
    gate.close()
    assert len(jev.calls) == 2


def test_close_is_bounded_when_jev_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeStore().install(monkeypatch)
    FakeJev(KEEP, sleep_s=0.5).install(monkeypatch)
    gate = _gate("shadow")
    for i in range(4):
        gate.before_draft(make_post(f"p{i}"))
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(engager_gate.log, "info", lambda _e, **kw: logged.append(kw))
    gate.close(timeout_s=0.1)
    assert logged[-1]["undrained"] >= 3  # type: ignore[operator]
    join_gate_threads()  # let the in-flight call finish under the fakes


# --- enforce -----------------------------------------------------------------


def test_enforce_skips_and_stamps_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(SKIP_ALL).install(monkeypatch)
    assert _gate("enforce").before_draft(make_post()) is True
    assert store.outcomes == [("https://x/p/p1", "skipped_by_gate")]


def test_enforce_reuses_stored_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore(stored={"https://x/p/p1": True, "https://x/p/p2": False})
    store.install(monkeypatch)
    jev = FakeJev(KEEP).install(monkeypatch)
    gate = _gate("enforce")
    assert gate.before_draft(make_post("p1")) is True
    assert gate.before_draft(make_post("p2")) is False
    assert jev.calls == []  # never re-asked
    assert store.outcomes == [("https://x/p/p1", "skipped_by_gate")]


def test_enforce_breaker_trips_after_two_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    jev = FakeJev(None).install(monkeypatch)
    gate = _gate("enforce")
    for i in range(6):
        assert gate.before_draft(make_post(f"p{i}")) is False
    assert len(jev.calls) == 2
    assert gate.budget.tripped == "circuit_breaker"
    # Failed calls are rows: error marker, empty answers, never would_skip.
    assert [(d.error, d.answers, d.would_skip) for d in store.decisions] == [
        (decisions_db.ERROR_JEV_CALL_FAILED, {}, False)
    ] * 2


def test_enforce_time_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeStore().install(monkeypatch)
    jev = FakeJev(KEEP).install(monkeypatch)
    ticks = iter(float(i * 40) for i in range(100))  # every call "takes" 40s
    gate = _gate("enforce", budget_s=60.0, clock=lambda: next(ticks))
    for i in range(5):
        gate.before_draft(make_post(f"p{i}"))
    assert len(jev.calls) == 2  # 40s, 80s -> over the 60s budget
    assert gate.budget.tripped == "time_budget"


# --- outcome bookkeeping -----------------------------------------------------


def test_outcomes_only_for_posts_this_run_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(KEEP).install(monkeypatch)
    gate = _gate("enforce", max_calls=1)
    gate.before_draft(make_post("p1"))
    gate.before_draft(make_post("p2"))  # capped: no row
    gate.after_draft(make_post("p1"), drafted=True, reason=None)
    gate.after_draft(make_post("p2"), drafted=True, reason=None)
    assert store.outcomes == [("https://x/p/p1", "engaged")]


def test_after_draft_maps_drafter_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(KEEP).install(monkeypatch)
    gate = _gate("enforce")
    for pid, drafted, reason in [
        ("a", True, None),
        ("b", False, draft_helper.AGENT_DECLINED),
        ("c", False, draft_helper.DRAFT_FAILED),
    ]:
        gate.before_draft(make_post(pid))
        gate.after_draft(make_post(pid), drafted=drafted, reason=reason)
    assert [o for _k, o in store.outcomes] == ["engaged", "declined", "drafter_error"]


def test_outcome_mapping_pins_draft_helper_constants() -> None:
    assert engager_gate.DRAFTER_ERROR_REASONS == {
        draft_helper.DRAFT_FAILED,
        draft_helper.DRAFT_BLANK,
    }
    assert engager_gate.VOICE_FAILED_REASON == draft_helper.VOICE_FAILED
    assert outcome_for(drafted=False, reason=draft_helper.VOICE_FAILED) == "engaged"
    assert outcome_for(drafted=False, reason=None) == "declined"


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_gate_swallows_errors(monkeypatch: pytest.MonkeyPatch, mode: GateMode) -> None:
    def _boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(decisions_db, "lookup_decision", _boom)
    monkeypatch.setattr(decisions_db, "record_outcome", _boom)
    gate = _gate(mode)
    assert gate.before_draft(make_post()) is False
    gate.after_draft(make_post(), drafted=True, reason=None)  # must not raise
    gate.close()


def test_off_mode_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    jev = FakeJev(KEEP).install(monkeypatch)
    gate = _gate("off")
    assert gate.before_draft(make_post()) is False
    gate.close()
    assert jev.calls == []
