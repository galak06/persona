"""Retry behaviour for the trend-scout stage — `execute_trends_crew`.

The bug this covers, observed live 2026-08-27: DeepSeek emitted a 35,001-char
response that opened with chain-of-thought prose, ran out of output budget,
and truncated mid-object (23 open braces, 21 closed). Unrecoverable by any
repair layer, and it zeroed the entire scout run -- no signals, no ideas,
exit 1. The same prompt succeeded on a re-roll minutes earlier, so a single
failure is a dice roll rather than an impossible request.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from lib.crew.trends import execute as trends_execute
from lib.crew.trends.execute import RETRY_INSTRUCTION, execute_trends_crew
from lib.crew.trends.models import TrendsOutput

_GOOD = '{"signals": [{"keyword": "dental chews", "category": "Dental Care", '
_GOOD += '"opportunity_type": "discovery", "score": 70.0, "reason": "r"}]}'

# The real shape of the failure: prose, then JSON that never closes.
_TRUNCATED = 'Let me review what I have.\n\nLet me finalize.{\n "signals": [\n {\n "keyword": "d'


class _Output:
    def __init__(self, raw: str | None) -> None:
        self.raw = raw


class _Task:
    def __init__(self) -> None:
        self.description = "ORIGINAL"
        self.output: _Output | None = None


class _FakeCrew:
    """Stands in for `crewai.Crew`, replaying a scripted sequence of outputs."""

    script: ClassVar[list[Any]] = []
    seen_descriptions: ClassVar[list[str]] = []

    def __init__(self, agents: Any, tasks: Any, verbose: bool = False) -> None:
        self._task = tasks[0]

    def kickoff(self) -> None:
        _FakeCrew.seen_descriptions.append(self._task.description)
        step = _FakeCrew.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self._task.output = _Output(step)


@pytest.fixture(autouse=True)
def _fake_crew(monkeypatch: pytest.MonkeyPatch) -> None:
    import crewai

    _FakeCrew.script = []
    _FakeCrew.seen_descriptions = []
    monkeypatch.setattr(crewai, "Crew", _FakeCrew)


def test_clean_first_response_does_not_retry() -> None:
    _FakeCrew.script = [_GOOD]
    result = execute_trends_crew(object(), _Task())
    assert isinstance(result, TrendsOutput)
    assert len(_FakeCrew.seen_descriptions) == 1


def test_truncated_response_is_retried_and_recovers() -> None:
    """The live failure: attempt 1 truncates, attempt 2 succeeds."""
    _FakeCrew.script = [_TRUNCATED, _GOOD]
    result = execute_trends_crew(object(), _Task())
    assert isinstance(result, TrendsOutput)
    assert result.signals[0].keyword == "dental chews"
    assert len(_FakeCrew.seen_descriptions) == 2


def test_retry_tells_the_model_what_went_wrong() -> None:
    """A bare re-roll wastes the attempt: the base prompt ALREADY forbids a
    preamble and the model ignored it, so the retry has to name the failure."""
    _FakeCrew.script = [_TRUNCATED, _GOOD]
    execute_trends_crew(object(), _Task())
    first, second = _FakeCrew.seen_descriptions
    assert RETRY_INSTRUCTION not in first
    assert RETRY_INSTRUCTION in second
    assert "truncated mid-object" in second


def test_gives_up_after_max_attempts() -> None:
    _FakeCrew.script = [_TRUNCATED, _TRUNCATED, _TRUNCATED]
    assert execute_trends_crew(object(), _Task(), max_attempts=3) is None
    assert len(_FakeCrew.seen_descriptions) == 3


def test_kickoff_network_error_is_retried_too() -> None:
    """Same reasoning as the engager navigation retry: a transient network
    failure must not cost the whole run."""
    _FakeCrew.script = [RuntimeError("connection reset"), _GOOD]
    assert isinstance(execute_trends_crew(object(), _Task()), TrendsOutput)
    assert len(_FakeCrew.seen_descriptions) == 2


def test_task_description_is_restored_for_the_caller() -> None:
    """The caller owns the Task and must not be left holding retry scaffolding
    -- a leaked instruction would ride along into every later run."""
    task = _Task()
    _FakeCrew.script = [_TRUNCATED, _GOOD]
    execute_trends_crew(object(), task)
    assert task.description == "ORIGINAL"


def test_description_restored_even_when_every_attempt_fails() -> None:
    task = _Task()
    _FakeCrew.script = [_TRUNCATED, _TRUNCATED, _TRUNCATED]
    execute_trends_crew(object(), task, max_attempts=3)
    assert task.description == "ORIGINAL"


def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(ValueError):
        execute_trends_crew(object(), _Task(), max_attempts=0)


def test_module_exposes_a_sane_default_attempt_count() -> None:
    assert trends_execute.DEFAULT_MAX_ATTEMPTS >= 2
