"""Tests for lib.crew.context -- prompt/context-building, no CrewAI/DB/network.
# ruff: noqa: S101
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.crew.context import (
    brand_identity_summary,
    brand_voice_summary,
    seed_keywords_summary,
    serialize_opportunities,
)
from lib.gsc_scout_scoring import GscOpportunity, KeywordSeed


def test_brand_identity_summary_includes_all_present_fields() -> None:
    config = {
        "site": {
            "name": "DogFoodAndFun",
            "brand_persona": "Nalla's Dad",
            "mascot_name": "Nalla",
            "niche": "dog food reviews, GPS trackers",
            "target_audience": "active dog owners",
        }
    }
    summary = brand_identity_summary(config)
    assert "DogFoodAndFun" in summary
    assert "Nalla's Dad" in summary
    assert "Nalla" in summary
    assert "dog food reviews, GPS trackers" in summary
    assert "active dog owners" in summary


def test_brand_identity_summary_tolerates_missing_fields() -> None:
    assert brand_identity_summary({}) == "Brand: "
    assert brand_identity_summary({"site": {"name": "X"}}) == "Brand: X"


def test_brand_voice_summary_prefers_current_reorg_path(tmp_path: Path) -> None:
    current = tmp_path / "data" / "config" / "brand_voice_guide.md"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("CURRENT voice text", encoding="utf-8")
    legacy = tmp_path / "data" / "brand_voice_guide.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("LEGACY voice text", encoding="utf-8")

    assert brand_voice_summary(tmp_path) == "CURRENT voice text"


def test_brand_voice_summary_falls_back_to_legacy_path(tmp_path: Path) -> None:
    legacy = tmp_path / "data" / "brand_voice_guide.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("LEGACY voice text", encoding="utf-8")

    assert brand_voice_summary(tmp_path) == "LEGACY voice text"


def test_brand_voice_summary_missing_file_returns_empty_string(tmp_path: Path) -> None:
    assert brand_voice_summary(tmp_path) == ""


def test_brand_voice_summary_truncates_long_text(tmp_path: Path) -> None:
    current = tmp_path / "data" / "config" / "brand_voice_guide.md"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("line one\n" * 500, encoding="utf-8")

    summary = brand_voice_summary(tmp_path, max_chars=100)
    assert len(summary) <= 130  # small slack for the "...(truncated)" suffix
    assert summary.endswith("...(truncated)")


def test_seed_keywords_summary_dedupes_and_sorts() -> None:
    seeds = [
        KeywordSeed(keyword="dog food"),
        KeywordSeed(keyword="dog food"),  # duplicate
        KeywordSeed(keyword="gps tracker"),
    ]
    assert seed_keywords_summary(seeds) == "dog food, gps tracker"


def test_seed_keywords_summary_empty_list() -> None:
    assert seed_keywords_summary([]) == ""


def test_serialize_opportunities_round_trips_expected_fields() -> None:
    opportunities = [
        GscOpportunity(
            keyword="dog food",
            category="content_analysis:primary_keywords",
            opportunity_type="optimize",
            score=82.5,
            matched_query="best dog food",
            best_position=12.0,
            impressions=150,
            cannibalization_urls=(),
            reason="optimize opportunity for 'dog food'",
        )
    ]
    parsed = json.loads(serialize_opportunities(opportunities))
    assert parsed == [
        {
            "keyword": "dog food",
            "category": "content_analysis:primary_keywords",
            "opportunity_type": "optimize",
            "score": 82.5,
            "best_position": 12.0,
            "impressions": 150,
            "reason": "optimize opportunity for 'dog food'",
        }
    ]


def test_serialize_opportunities_empty_list_is_empty_json_array() -> None:
    assert serialize_opportunities([]) == "[]"
