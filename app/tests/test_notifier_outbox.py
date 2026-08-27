"""Undeliverable-alert outbox — `lib.notifier_outbox`.

The circular failure this guards: the alert about a DNS outage is itself a
network call that the same outage kills, so the run you most needed to hear
about goes silent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import notifier_outbox


@pytest.fixture
def outbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "notifier_outbox.jsonl"
    monkeypatch.setattr(notifier_outbox, "outbox_path", lambda: path)
    return path


def test_enqueue_then_drain_delivers_in_order(outbox: Path) -> None:
    notifier_outbox.enqueue("first")
    notifier_outbox.enqueue("second")
    sent: list[str] = []
    assert notifier_outbox.drain(lambda m, _s: (sent.append(m), True)[1]) == 2
    assert sent == ["first", "second"]
    assert notifier_outbox.pending_count() == 0


def test_silent_flag_survives_the_round_trip(outbox: Path) -> None:
    notifier_outbox.enqueue("quiet", silent=True)
    seen: list[bool] = []
    notifier_outbox.drain(lambda _m, s: (seen.append(s), True)[1])
    assert seen == [True]


def test_a_still_broken_network_keeps_everything(outbox: Path) -> None:
    notifier_outbox.enqueue("a")
    notifier_outbox.enqueue("b")
    assert notifier_outbox.drain(lambda _m, _s: False) == 0
    assert notifier_outbox.pending_count() == 2


def test_drain_stops_at_the_first_failure_and_preserves_order(outbox: Path) -> None:
    """Partial success must not reorder or lose the tail."""
    for msg in ("a", "b", "c"):
        notifier_outbox.enqueue(msg)
    attempts: list[str] = []

    def send(message: str, _silent: bool) -> bool:
        attempts.append(message)
        return message == "a"

    assert notifier_outbox.drain(send) == 1
    assert attempts == ["a", "b"]
    assert notifier_outbox.pending_count() == 2


def test_a_raising_sender_is_treated_as_failure_not_a_crash(outbox: Path) -> None:
    notifier_outbox.enqueue("a")

    def boom(_m: str, _s: bool) -> bool:
        raise RuntimeError("network down")

    assert notifier_outbox.drain(boom) == 0
    assert notifier_outbox.pending_count() == 1


def test_outbox_is_bounded_and_drops_oldest_first(outbox: Path) -> None:
    """A multi-day outage must not grow the file without limit; the recent
    alerts are the ones still worth reading."""
    for i in range(notifier_outbox.MAX_PENDING + 10):
        notifier_outbox.enqueue(f"msg-{i}")
    assert notifier_outbox.pending_count() == notifier_outbox.MAX_PENDING
    sent: list[str] = []
    notifier_outbox.drain(lambda m, _s: (sent.append(m), True)[1])
    assert sent[0] == "msg-10"  # the first 10 were dropped, not the last


def test_corrupt_line_does_not_lose_the_rest(outbox: Path) -> None:
    notifier_outbox.enqueue("good")
    outbox.write_text(outbox.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    sent: list[str] = []
    assert notifier_outbox.drain(lambda m, _s: (sent.append(m), True)[1]) == 1
    assert sent == ["good"]


def test_empty_message_is_not_queued(outbox: Path) -> None:
    assert notifier_outbox.enqueue("") is False
    assert notifier_outbox.pending_count() == 0


def test_no_brand_context_degrades_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-brand-scoped callers must not blow up on a missing brand dir."""
    monkeypatch.setattr(notifier_outbox, "outbox_path", lambda: None)
    assert notifier_outbox.enqueue("x") is False
    assert notifier_outbox.pending_count() == 0
    assert notifier_outbox.drain(lambda _m, _s: True) == 0
