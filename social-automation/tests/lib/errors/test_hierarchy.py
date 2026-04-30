"""Tests for the exception taxonomy.

The contract: every concrete exception is catchable as exactly one of
the categorical bases. Callers rely on this to dispatch retry vs.
escalate vs. crash without knowing every concrete class.
"""

from __future__ import annotations

import pytest

from lib.errors import (
    AuthError,
    BugError,
    ConfigurationError,
    NetworkError,
    NotFoundError,
    PermanentError,
    PostFailedError,
    RateLimitedError,
    RetryableError,
    SocialAutomationError,
    TokenExpiredError,
    ValidationFailedError,
)


class TestUmbrellaBase:
    """Every concrete error is a SocialAutomationError."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConfigurationError,
            NetworkError,
            AuthError,
            TokenExpiredError,
            RateLimitedError,
            ValidationFailedError,
            PostFailedError,
            NotFoundError,
        ],
    )
    def test_concrete_inherits_from_umbrella(self, exc_cls: type[SocialAutomationError]) -> None:
        assert issubclass(exc_cls, SocialAutomationError)


class TestRetryableCategory:
    """Retryable errors are caught by `except RetryableError`."""

    @pytest.mark.parametrize(
        "exc_cls",
        [NetworkError, TokenExpiredError, RateLimitedError],
    )
    def test_retryable_caught_as_retryable(self, exc_cls: type[RetryableError]) -> None:
        with pytest.raises(RetryableError):
            raise exc_cls("test")

    def test_retryable_not_caught_as_permanent(self) -> None:
        with pytest.raises(NetworkError):  # not PermanentError
            try:
                raise NetworkError("test")
            except PermanentError:
                pytest.fail("NetworkError must not be a PermanentError")


class TestPermanentCategory:
    """Permanent errors are caught by `except PermanentError`."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConfigurationError,
            AuthError,
            ValidationFailedError,
            PostFailedError,
            NotFoundError,
        ],
    )
    def test_permanent_caught_as_permanent(self, exc_cls: type[PermanentError]) -> None:
        with pytest.raises(PermanentError):
            raise exc_cls(
                "test",
                **(
                    {"violations": ["v"]}
                    if exc_cls is ValidationFailedError
                    else {"platform": "p"}
                    if exc_cls is PostFailedError
                    else {}
                ),
            )


class TestContextCarriage:
    """The base attaches a `context` dict for structured logging."""

    def test_context_defaults_empty(self) -> None:
        err = NetworkError("boom")
        assert err.context == {}

    def test_context_round_trips(self) -> None:
        err = NetworkError("boom", context={"correlation_id": "x:1", "platform": "fb"})
        assert err.context == {"correlation_id": "x:1", "platform": "fb"}

    def test_context_is_copied_not_aliased(self) -> None:
        original: dict[str, object] = {"k": "v"}
        err = NetworkError("boom", context=original)
        original["k"] = "mutated"
        assert err.context == {"k": "v"}, "context must be defensively copied"


class TestRateLimitedRetryAfter:
    def test_default_none(self) -> None:
        err = RateLimitedError("limit")
        assert err.retry_after_seconds is None

    def test_carries_seconds(self) -> None:
        err = RateLimitedError("limit", retry_after_seconds=120)
        assert err.retry_after_seconds == 120


class TestValidationFailedViolations:
    def test_violations_list_required(self) -> None:
        err = ValidationFailedError("bad", violations=["medical_jargon", "no_question"])
        assert err.violations == ["medical_jargon", "no_question"]

    def test_violations_copied(self) -> None:
        original = ["v1"]
        err = ValidationFailedError("bad", violations=original)
        original.append("v2")
        assert err.violations == ["v1"]


class TestPostFailedPlatform:
    def test_platform_required(self) -> None:
        err = PostFailedError("rejected", platform="instagram")
        assert err.platform == "instagram"


class TestBugErrorIsolated:
    """BugError is its own category — not retryable, not permanent."""

    def test_not_retryable(self) -> None:
        with pytest.raises(BugError):
            try:
                raise BugError("invariant violated")
            except (RetryableError, PermanentError):
                pytest.fail("BugError must be neither RetryableError nor PermanentError")

    def test_still_caught_by_umbrella(self) -> None:
        with pytest.raises(SocialAutomationError):
            raise BugError("invariant")
