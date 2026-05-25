"""Unit tests for the hook-first caption validator. No network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from generators.caption_validator import validate_hook  # noqa: E402
from lib.errors.validation import ValidationFailedError  # noqa: E402

_DEFAULT_BLOCKLIST = [
    r"^New on the blog",
    r"^Check out",
    r"^Read (about|more)",
    r"^Today I",
]


def test_rejects_new_on_the_blog() -> None:
    with pytest.raises(ValidationFailedError, match="hook"):
        validate_hook(
            "New on the blog — turkey jerky for dogs. Nalla loved it.",
            _DEFAULT_BLOCKLIST,
        )


def test_rejects_check_out() -> None:
    with pytest.raises(ValidationFailedError, match="hook"):
        validate_hook("Check out our latest recipe", _DEFAULT_BLOCKLIST)


def test_rejects_read_more() -> None:
    with pytest.raises(ValidationFailedError, match="hook"):
        validate_hook("Read more about dog nutrition today.", _DEFAULT_BLOCKLIST)


def test_rejects_today_i() -> None:
    with pytest.raises(ValidationFailedError, match="hook"):
        validate_hook("Today I made turkey jerky for Nalla.", _DEFAULT_BLOCKLIST)


def test_accepts_concrete_moment() -> None:
    validate_hook(
        "Nalla turned her nose up at her dinner. Same food, 4 years.",
        _DEFAULT_BLOCKLIST,
    )


def test_case_insensitive() -> None:
    with pytest.raises(ValidationFailedError):
        validate_hook("new on the blog: a recipe", _DEFAULT_BLOCKLIST)


def test_empty_blocklist_passes() -> None:
    validate_hook("Check out this thing", [])


def test_empty_caption_passes() -> None:
    validate_hook("", _DEFAULT_BLOCKLIST)
