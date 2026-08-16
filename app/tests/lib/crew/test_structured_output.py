"""Tests for `lib.crew.structured_output` — the parse contract all 8 crew stages share.

Before consolidation each stage had its own copy, and the copies had diverged
into three tiers of robustness (see `docs/adr/0007-one-crew-output-parser.md`).
These tests pin the contract at the one place that now implements it, so a
regression here is caught once rather than in eight stage-specific suites that
each only covered their own tier.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from lib.crew.structured_output import parse_structured_output


class _Verdict(BaseModel):
    passed: bool
    score: float


class _RecordingLogger:
    """Captures the structured warning lines a stage would emit."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.warnings.append((event, dict(kwargs)))

    def events(self) -> list[str]:
        return [e for e, _ in self.warnings]


def _parse(raw: str | None) -> tuple[_Verdict | None, _RecordingLogger]:
    log = _RecordingLogger()
    return parse_structured_output(raw, _Verdict, event="editor", log=log), log


def test_parses_a_clean_object() -> None:
    result, log = _parse('{"passed": true, "score": 91}')
    assert result == _Verdict(passed=True, score=91)
    assert log.events() == [], "a clean parse must not warn"


def test_strips_a_markdown_fence() -> None:
    """The most common real failure: the model wraps its JSON in ```json."""
    result, _ = _parse('```json\n{"passed": false, "score": 40}\n```')
    assert result == _Verdict(passed=False, score=40)


def test_extracts_an_object_buried_in_prose() -> None:
    """Was the extract-only tier's whole job; now every stage gets it."""
    result, _ = _parse(
        'Sure! Here is the verdict:\n{"passed": true, "score": 80}\nHope that helps.'
    )
    assert result == _Verdict(passed=True, score=80)


def test_recovers_a_trailing_comma() -> None:
    """The tier gap that motivated this module.

    `writer` recovered this via `json_recovery.loads_lenient`; `editor`,
    `categorizer` and `products` called plain `json.loads` and binned the
    output. Same input, opposite outcome, purely by which stage you were in.
    """
    result, log = _parse('{"passed": true, "score": 91,}')
    assert result == _Verdict(passed=True, score=91)
    assert "editor_json_recovered" in log.events(), (
        "a recovered parse must never look like a clean one -- the model emitted "
        "damaged JSON and the repair may have left an artifact"
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t "])
def test_empty_output_returns_none_and_warns(raw: str | None) -> None:
    result, log = _parse(raw)
    assert result is None
    assert log.events() == ["editor_empty_output"]


def test_unrecoverable_json_returns_none_and_warns() -> None:
    result, log = _parse("this is not JSON at all")
    assert result is None
    assert log.events() == ["editor_json_decode_failed"]


def test_decode_failure_logs_the_full_text_not_an_excerpt() -> None:
    """A 200-char excerpt was proven useless: the error position was always
    past it, so two consecutive live failures gave no way to see what broke."""
    raw = "x" * 500 + "{not json}"
    _, log = _parse(raw)
    event, fields = log.warnings[0]
    assert event == "editor_json_decode_failed"
    assert fields["raw_output"] == raw


def test_schema_mismatch_returns_none_and_warns() -> None:
    """Valid JSON, wrong shape -- a distinct failure from a decode error."""
    result, log = _parse('{"passed": "yes please", "score": "high"}')
    assert result is None
    assert log.events() == ["editor_schema_validation_failed"]


def test_never_raises_on_any_input() -> None:
    """Every stage's contract is "return None, never crash the run"."""
    for raw in ['{"unclosed": ', "[]", "null", "42", '"a string"', "{}", "```", "\x00"]:
        assert parse_structured_output(raw, _Verdict, event="t", log=_RecordingLogger()) is None


def test_event_prefix_attributes_the_failure_to_its_stage() -> None:
    """Eight stages share one implementation; the log must still name which."""
    log = _RecordingLogger()
    parse_structured_output("nope", _Verdict, event="socialpost", log=log)
    assert log.events() == ["socialpost_json_decode_failed"]


def test_logger_is_optional() -> None:
    """Stages pass their own, but the module must work without one."""
    assert parse_structured_output("nope", _Verdict, event="t") is None
