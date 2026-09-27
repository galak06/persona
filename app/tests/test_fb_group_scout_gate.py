"""fb-group-scout + the Jev group gate, end to end through `main()`.

The scout runs for real (candidate collection, scoring cut, admission
classification, rank cut, approval, join loop); only the browser, the
state files, the notifier and Jev/Postgres are faked. The contract under
test: a SHADOW gate -- even one that would skip every group, answers slowly,
or explodes -- leaves every join, skip and pending write identical to a run
without a gate, and adds no wall-clock time. Each decision row still gets
the scout's real outcome.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from scripts import fb_group_scout as scout

from lib.decisions import decisions_db
from lib.decisions.scout_gate import JevGroupGate
from lib.decisions.scout_hooks import item_key
from lib.group_discovery import approval
from lib.group_discovery.relevance import ScoutRules
from tests.test_group_gate import FakeDecide, _answers
from tests.test_scout_gate import FakeStore, SlowDecide, _join_worker

# name -> (heuristic score, privacy, admission likelihood)
SEARCH = {
    "A": (80, "public", "easy"),
    "B": (70, "private", None),
    "C": (60, "public", "closed"),
    "D": (30, "public", "easy"),  # below MIN_SCORE
    "E": (55, "private", "admin_approval"),
    "F": (45, "public", "unknown"),
}
PENDING_P = {
    "url": "https://www.facebook.com/groups/P",
    "name": "P",
    "privacy": "public",
    "score": 50,
    "status": "pending",
    "member_count": 5000,
    "description": "",
    "post_frequency": "",
    "found_via_query": "q",
}


def _cards() -> list[dict[str, Any]]:
    return [
        {
            "url": f"https://www.facebook.com/groups/{name}",
            "name": name,
            "privacy": privacy,
            "member_text": "5K members",
            "post_frequency": "",
            "description": f"{name} dogs",
            "_s": score,
        }
        for name, (score, privacy, _) in SEARCH.items()
    ]


class FakePage:
    url = "https://www.facebook.com/"

    def goto(self, *_a: object, **_k: object) -> None:
        return None


class FakeSession:
    def is_authenticated(self) -> bool:
        return True

    @contextmanager
    def page(self) -> Iterator[FakePage]:
        yield FakePage()


class Trace:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def add(self, kind: str, value: Any) -> None:
        self.events.append((kind, value))


@pytest.fixture
def trace(monkeypatch: pytest.MonkeyPatch) -> Trace:
    t = Trace()
    noop = lambda *_a, **_k: None  # noqa: E731
    monkeypatch.setattr(time, "sleep", noop)  # the scout's page-settle waits
    for name in (
        "skill_started",
        "skill_finished",
        "skill_skipped",
        "log_error",
        "pace_between_queries",
        "save_pending",
    ):
        monkeypatch.setattr(scout, name, noop)
    monkeypatch.setattr(scout, "load_last_run", lambda: {})
    monkeypatch.setattr(scout, "compute_budget", lambda bypass_daily=False: (2, 2, 2))
    monkeypatch.setattr(scout, "load_scout_rules", lambda: ScoutRules(search_queries=("q",)))
    monkeypatch.setattr(scout, "load_known_groups", lambda: set())
    monkeypatch.setattr(scout, "competitor_queries", lambda: [])
    monkeypatch.setattr(scout, "load_competitors", lambda: [])
    monkeypatch.setattr(scout, "off_target_reason", lambda _c, _r: None)
    monkeypatch.setattr(scout, "score_group", lambda card, **_k: card["_s"])
    monkeypatch.setattr(scout, "search_groups", lambda _p, _q: _cards())

    def classify(text: str) -> dict[str, str] | None:
        likelihood = SEARCH[text.split("\n", 1)[0]][2]
        return None if likelihood is None else {"likelihood": likelihood, "reason": "r"}

    monkeypatch.setattr(scout, "classify_admission_likelihood", classify)
    monkeypatch.setattr(scout, "load_pending", lambda: [dict(PENDING_P)])

    def add_to_pending(groups: list[dict[str, Any]], _known: object) -> int:
        t.add("pending", [g["name"] for g in groups])
        return 0

    monkeypatch.setattr(scout, "add_to_pending", add_to_pending)
    monkeypatch.setattr(scout, "remove_from_pending", lambda urls: t.add("unpend", list(urls)))
    monkeypatch.setattr(scout, "save_last_run", lambda d: t.add("last_run", _stable(d)))

    def try_join(_page: object, url: str) -> str:
        t.add("join", url)
        return "clicked:request" if url.endswith(("B", "E")) else "clicked:join group"

    monkeypatch.setattr(approval, "try_join", try_join)
    monkeypatch.setattr(approval, "pace_between_joins", lambda is_last=False: 0.0)
    monkeypatch.setattr(approval, "log_join_request", lambda g, s: t.add("logged", (g["name"], s)))
    monkeypatch.setattr(approval, "append_to_tracker", noop)
    return t


def _stable(last_run: dict[str, Any]) -> dict[str, Any]:
    rec = dict(last_run.get("fb_group_scout", {}))
    rec.pop("last_run_at", None)
    return rec


def _baseline(trace: Trace) -> list[tuple[str, Any]]:
    scout.main(FakeSession(), preselected="all")  # type: ignore[arg-type]
    events = list(trace.events)
    trace.events.clear()
    return events


def _shadow(decide: Any, **kw: Any) -> JevGroupGate:
    return JevGroupGate(brand_id="b1", mode="shadow", brand_focus="dog food", decide=decide, **kw)


def test_baseline_is_what_the_scout_really_does(trace: Trace) -> None:
    events = _baseline(trace)
    joins = [v for k, v in events if k == "join"]
    assert joins == ["https://www.facebook.com/groups/P", "https://www.facebook.com/groups/A"]
    assert ("pending", ["B", "E", "F"]) in events  # unapproved, kept for the next run
    assert (
        "last_run",
        {"groups_found": 4, "groups_approved": 2, "join_requests_sent": 2, "status": "success"},
    ) in events


def test_shadow_gate_that_skips_everything_changes_nothing(
    trace: Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _baseline(trace)
    store = FakeStore().install(monkeypatch)
    gate = _shadow(FakeDecide(_answers(match="off_topic", match_confidence=1.0, active=0.0)))
    scout.main(FakeSession(), preselected="all", group_gate=gate)  # type: ignore[arg-type]
    assert trace.events == baseline

    # Every screened group has a would-skip row stamped with the scout's fate.
    assert all(r.would_skip for r in store.decisions)
    assert len(store.decisions) == 7  # 6 searched + the pending one
    by_name = {k.rsplit("/", 1)[1]: v for k, v in store.final_outcomes().items()}
    assert by_name == {
        "p": "joined",
        "a": "joined",
        "b": "skipped_cap",
        "c": "skipped_admission_closed",
        "d": "skipped_low_score",
        "e": "skipped_cap",
        "f": "skipped_cap",
    }


def test_slow_jev_adds_no_time_and_changes_nothing(
    trace: Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _baseline(trace)
    FakeStore().install(monkeypatch)
    gate = _shadow(SlowDecide(0.5, match="off_topic", match_confidence=1.0), drain_timeout_s=0.1)
    started = time.monotonic()
    scout.main(FakeSession(), preselected="all", group_gate=gate)  # type: ignore[arg-type]
    elapsed = time.monotonic() - started
    assert trace.events == baseline
    assert elapsed < 1.0  # 7 serial Jev calls would be 3.5s; the run never waits on them
    _join_worker(gate)  # let stragglers land while the fake store is installed


def test_exploding_gate_changes_nothing(trace: Trace, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _baseline(trace)

    def boom(*_a: object, **_k: object) -> Any:
        raise RuntimeError("jev + db down")

    for name in ("record_decision", "lookup_decision", "record_outcome"):
        monkeypatch.setattr(decisions_db, name, boom)
    for mode in ("shadow", "enforce"):
        gate = JevGroupGate(brand_id="b1", mode=mode, brand_focus="f", decide=boom)
        scout.main(FakeSession(), preselected="all", group_gate=gate)  # type: ignore[arg-type]
        assert trace.events == baseline
        trace.events.clear()


def test_dry_run_ignores_the_gate(trace: Trace, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    decide = FakeDecide(_answers())
    scout.main(FakeSession(), dry_run=True, group_gate=_shadow(decide))  # type: ignore[arg-type]
    assert decide.calls == [] and store.decisions == []


def test_enforce_only_removes_candidates(trace: Trace, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)

    class SkipA(FakeDecide):
        def __call__(self, state: Any, questions: Any) -> Any:
            self.answers = _answers(active=0.0 if state["name"] == "A" else 2.0)
            return super().__call__(state, questions)

    gate = JevGroupGate(brand_id="b1", mode="enforce", brand_focus="f", decide=SkipA(None))
    scout.main(FakeSession(), preselected="all", group_gate=gate)  # type: ignore[arg-type]
    joins = [v for k, v in trace.events if k == "join"]
    assert joins == ["https://www.facebook.com/groups/P", "https://www.facebook.com/groups/B"]
    assert store.final_outcomes()[item_key({"url": "https://www.facebook.com/groups/A"})] == (
        "skipped_by_gate"
    )
