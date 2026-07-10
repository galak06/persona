"""Tests for lib.observability.correlation_id."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lib.observability.correlation_id import (
    correlation_id,
    new_correlation_id,
    set_correlation_id,
    with_correlation_id,
)


@pytest.fixture(autouse=True)
def _reset_id() -> None:
    """Each test starts from the default. ContextVar bleeds across tests
    in the same thread otherwise."""
    correlation_id.set("<unset>")


class TestSetCorrelationId:
    def test_default_is_unset(self) -> None:
        assert correlation_id.get() == "<unset>"

    def test_set_persists(self) -> None:
        set_correlation_id("comment-poster:2026-04-30:fb-abc")
        assert correlation_id.get() == "comment-poster:2026-04-30:fb-abc"


class TestWithCorrelationId:
    def test_scoped_value_set_and_restored(self) -> None:
        set_correlation_id("outer")
        with with_correlation_id("inner") as cid:
            assert cid == "inner"
            assert correlation_id.get() == "inner"
        assert correlation_id.get() == "outer"

    def test_restores_default_when_no_outer(self) -> None:
        with with_correlation_id("inner"):
            assert correlation_id.get() == "inner"
        assert correlation_id.get() == "<unset>"

    def test_restores_on_exception(self) -> None:
        with pytest.raises(RuntimeError), with_correlation_id("inner"):
            raise RuntimeError("boom")
        assert correlation_id.get() == "<unset>"


class TestNewCorrelationId:
    def test_format_with_skill_and_item(self) -> None:
        when = datetime(2026, 4, 30, tzinfo=UTC)
        with new_correlation_id("comment-poster", item_id="fb-abc", when=when) as cid:
            assert cid == "comment-poster:2026-04-30:fb-abc"
            assert correlation_id.get() == cid

    def test_format_without_item(self) -> None:
        when = datetime(2026, 4, 30, tzinfo=UTC)
        with new_correlation_id("recipe-publisher", when=when) as cid:
            assert cid == "recipe-publisher:2026-04-30"

    def test_uses_now_when_no_when_provided(self) -> None:
        with new_correlation_id("scout", item_id="x") as cid:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            assert cid.startswith(f"scout:{today}:")
            assert cid.endswith(":x")

    def test_restores_on_exit(self) -> None:
        with new_correlation_id("a"):
            pass
        assert correlation_id.get() == "<unset>"

    def test_value_attr_accessible(self) -> None:
        ctx = new_correlation_id("a", item_id="b", when=datetime(2026, 1, 1, tzinfo=UTC))
        assert ctx.value == "a:2026-01-01:b"

    def test_nested_contexts_restore_outer(self) -> None:
        when = datetime(2026, 4, 30, tzinfo=UTC)
        with new_correlation_id("outer", item_id="A", when=when):
            assert correlation_id.get() == "outer:2026-04-30:A"
            with new_correlation_id("inner", item_id="B", when=when):
                assert correlation_id.get() == "inner:2026-04-30:B"
            assert correlation_id.get() == "outer:2026-04-30:A"
        assert correlation_id.get() == "<unset>"
