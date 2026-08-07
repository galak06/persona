"""Unit tests for lib.crew.topic_similarity -- is_similar_topic.

Direct tests of the fuzzy-dedup predicate itself, independent of
`run_crew_scout`'s integration tests in test_crew_scout.py /
test_crew_scout_auto_approve.py.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.crew.topic_similarity import is_similar_topic


def test_is_similar_topic_exact_match_case_insensitive() -> None:
    existing = {"best dog food for sensitive stomachs"}
    assert is_similar_topic("Best Dog Food For Sensitive Stomachs", existing) is True


def test_is_similar_topic_fuzzy_match_above_threshold() -> None:
    existing = {"homemade dog food recipes for beginners"}
    assert is_similar_topic("Homemade Dog Food Recipe for Beginners", existing) is True


def test_is_similar_topic_distinct_topic_below_threshold() -> None:
    existing = {"homemade dog food recipes for beginners"}
    assert is_similar_topic("Best GPS Trackers for Anxious Dogs", existing) is False


def test_is_similar_topic_empty_existing_returns_false() -> None:
    assert is_similar_topic("Anything At All", set()) is False


def test_is_similar_topic_custom_threshold_changes_outcome() -> None:
    existing = {"how to fix your dog's gut"}
    borderline = "Fixing Your Dog's Gut Issues"

    assert is_similar_topic(borderline, existing, threshold=0.85) is False
    assert is_similar_topic(borderline, existing, threshold=0.6) is True
