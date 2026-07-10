# pyright: reportMissingImports=false
"""Unit tests for pipeline.checkpoint (the end-of-phase validation gate)."""
# ruff: noqa: S101

from __future__ import annotations

import pytest
from pipeline.checkpoint import CheckpointError, checkpoint


class _FakeLogger:
    """Captures structured-logging calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, /, **fields: object) -> None:
        self.calls.append(("info", event, fields))

    def error(self, event: str, /, **fields: object) -> None:
        self.calls.append(("error", event, fields))


def test_checkpoint_pass_logs_and_returns() -> None:
    log = _FakeLogger()
    checkpoint("seasonal_selection", ok=True, logger=log, selected=3)
    assert log.calls == [
        ("info", "checkpoint_pass", {"phase": "seasonal_selection", "selected": 3})
    ]


def test_checkpoint_fail_raises_and_logs() -> None:
    log = _FakeLogger()
    with pytest.raises(CheckpointError) as exc:
        checkpoint("seasonal_selection", ok=False, reason="bad", logger=log)
    assert exc.value.phase == "seasonal_selection"
    assert "bad" in str(exc.value)
    assert log.calls[0][0] == "error"


def test_checkpoint_default_logger_does_not_crash() -> None:
    # No injected logger -> stdlib structured fallback; happy path must not raise.
    checkpoint("seasonal_selection", ok=True, count=0)
