"""Tests for lib.runtime.health_check."""

from __future__ import annotations

import pytest

from lib.runtime.health_check import (
    HealthCheckResult,
    get_registered,
    register,
    run_health_checks,
    unregister,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Ensure each test starts with a clean registry."""
    for platform in list(get_registered()):
        unregister(platform)


class TestRegistry:
    def test_register_and_lookup(self) -> None:
        def probe() -> HealthCheckResult:
            return HealthCheckResult(platform="x", ok=True, detail="ok")

        register("x", probe)
        assert "x" in get_registered()

    def test_re_register_replaces(self) -> None:
        def probe_a() -> HealthCheckResult:
            return HealthCheckResult(platform="x", ok=True, detail="A")

        def probe_b() -> HealthCheckResult:
            return HealthCheckResult(platform="x", ok=True, detail="B")

        register("x", probe_a)
        register("x", probe_b)
        result = run_health_checks(["x"])
        assert result is True

    def test_unregister(self) -> None:
        register("x", lambda: HealthCheckResult("x", True, "ok"))
        unregister("x")
        assert "x" not in get_registered()

    def test_unregister_unknown_is_noop(self) -> None:
        unregister("never-registered")  # must not raise


class TestRunHealthChecks:
    def test_all_pass_returns_true(self) -> None:
        register("a", lambda: HealthCheckResult("a", True, "ok"))
        register("b", lambda: HealthCheckResult("b", True, "ok"))
        assert run_health_checks(["a", "b"]) is True

    def test_one_fails_returns_false(self) -> None:
        register("a", lambda: HealthCheckResult("a", True, "ok"))
        register("b", lambda: HealthCheckResult("b", False, "down"))
        assert run_health_checks(["a", "b"]) is False

    def test_unregistered_platform_fails(self) -> None:
        assert run_health_checks(["nonexistent"]) is False

    def test_callback_receives_each_result(self) -> None:
        register("a", lambda: HealthCheckResult("a", True, "ok"))
        register("b", lambda: HealthCheckResult("b", False, "down"))
        results: list[HealthCheckResult] = []
        run_health_checks(["a", "b"], on_result=results.append)
        assert len(results) == 2
        assert results[0].platform == "a" and results[0].ok is True
        assert results[1].platform == "b" and results[1].ok is False

    def test_probe_exception_becomes_failed_result(self) -> None:
        def bad_probe() -> HealthCheckResult:
            raise RuntimeError("oops")

        register("a", bad_probe)
        results: list[HealthCheckResult] = []
        ok = run_health_checks(["a"], on_result=results.append)
        assert ok is False
        assert results[0].ok is False
        assert "oops" in results[0].detail
        assert "RuntimeError" in results[0].detail


class TestHealthCheckResult:
    def test_immutable(self) -> None:
        r = HealthCheckResult(platform="x", ok=True, detail="ok")
        with pytest.raises(AttributeError):
            r.ok = False  # type: ignore[misc]

    def test_latency_optional(self) -> None:
        r = HealthCheckResult(platform="x", ok=True, detail="ok")
        assert r.latency_ms is None

    def test_latency_carries(self) -> None:
        r = HealthCheckResult(platform="x", ok=True, detail="ok", latency_ms=42)
        assert r.latency_ms == 42
