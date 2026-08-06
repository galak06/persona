"""Tests for lib.gsc_scout_scoring -- pure functions, no DB/network/fixtures."""
# ruff: noqa: S101

from __future__ import annotations

from lib.gsc_scout_scoring import (
    KeywordSeed,
    rank_opportunities,
    score_keyword_seed,
)

_URL_A = "https://dogfoodandfun.com/post-a/"
_URL_B = "https://dogfoodandfun.com/post-b/"


def test_discovery_when_no_gsc_match() -> None:
    seed = KeywordSeed(keyword="frozen dog treats")
    opp = score_keyword_seed(seed, [], [], data_sufficient=True)
    assert opp is not None
    assert opp.opportunity_type == "discovery"
    assert opp.best_position is None
    assert opp.impressions == 0


def test_optimize_when_winnable_position_and_impressions() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {
            "wp_url": _URL_A,
            "gsc_query": "pumpkin dog biscuits",
            "position": 12.0,
            "impressions": 100,
        }
    ]
    opp = score_keyword_seed(seed, rows, [], data_sufficient=True)
    assert opp is not None
    assert opp.opportunity_type == "optimize"
    assert opp.best_position == 12.0
    assert opp.impressions == 100


def test_emerging_when_gsc_signal_but_not_winnable_band() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {
            "wp_url": _URL_A,
            "gsc_query": "pumpkin dog biscuits",
            "position": 45.0,
            "impressions": 100,
        }
    ]
    opp = score_keyword_seed(seed, rows, [], data_sufficient=True)
    assert opp is not None
    assert opp.opportunity_type == "emerging"


def test_low_impressions_keeps_it_emerging_not_optimize() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {"wp_url": _URL_A, "gsc_query": "pumpkin dog biscuits", "position": 12.0, "impressions": 10}
    ]
    opp = score_keyword_seed(seed, rows, [], data_sufficient=True)
    assert opp is not None
    assert opp.opportunity_type == "emerging"


def test_skipped_when_already_winning() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {"wp_url": _URL_A, "gsc_query": "pumpkin dog biscuits", "position": 3.0, "impressions": 500}
    ]
    assert score_keyword_seed(seed, rows, [], data_sufficient=True) is None


def test_skipped_on_cannibalization_two_owned_urls() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {
            "wp_url": _URL_A,
            "gsc_query": "pumpkin dog biscuits",
            "position": 12.0,
            "impressions": 50,
        },
        {
            "wp_url": _URL_B,
            "gsc_query": "pumpkin dog biscuits recipe",
            "position": 14.0,
            "impressions": 60,
        },
    ]
    assert score_keyword_seed(seed, rows, [], data_sufficient=True) is None


def test_skipped_when_already_covered_by_recent_post_title() -> None:
    seed = KeywordSeed(keyword="frozen dog treats")
    site_posts = [{"title": "The Best Frozen Dog Treats for Summer", "tags": []}]
    assert score_keyword_seed(seed, [], site_posts, data_sufficient=True) is None


def test_skipped_when_already_covered_by_recent_post_tag() -> None:
    seed = KeywordSeed(keyword="grain-free")
    site_posts = [{"title": "Unrelated Title", "tags": ["grain-free", "recipes"]}]
    assert score_keyword_seed(seed, [], site_posts, data_sufficient=True) is None


def test_insufficient_data_forces_discovery_and_penalizes_score() -> None:
    seed = KeywordSeed(keyword="pumpkin dog biscuits")
    rows = [
        {
            "wp_url": _URL_A,
            "gsc_query": "pumpkin dog biscuits",
            "position": 12.0,
            "impressions": 100,
        }
    ]
    sufficient_opp = score_keyword_seed(seed, rows, [], data_sufficient=True)
    insufficient_opp = score_keyword_seed(seed, rows, [], data_sufficient=False)
    assert sufficient_opp is not None and insufficient_opp is not None
    assert sufficient_opp.opportunity_type == "optimize"
    assert insufficient_opp.opportunity_type == "discovery"
    assert insufficient_opp.score < sufficient_opp.score
    assert "insufficient GSC history" in insufficient_opp.reason


def test_cluster_priority_bonus_outranks_lower_priority() -> None:
    high_priority = KeywordSeed(keyword="topic a", priority=1)
    low_priority = KeywordSeed(keyword="topic b", priority=9)
    opp_high = score_keyword_seed(high_priority, [], [], data_sufficient=True)
    opp_low = score_keyword_seed(low_priority, [], [], data_sufficient=True)
    assert opp_high is not None and opp_low is not None
    assert opp_high.score > opp_low.score


def test_rank_opportunities_dedupes_case_insensitively() -> None:
    seeds = [
        KeywordSeed(keyword="Pumpkin Dog Biscuits"),
        KeywordSeed(keyword="pumpkin dog biscuits"),
    ]
    ranked = rank_opportunities(seeds, [], [], data_sufficient=True)
    assert len(ranked) == 1


def test_rank_opportunities_respects_top_n_and_sorts_descending() -> None:
    seeds = [KeywordSeed(keyword=f"topic {i}", priority=i) for i in range(20)]
    ranked = rank_opportunities(seeds, [], [], data_sufficient=True, top_n=5)
    assert len(ranked) == 5
    scores = [o.score for o in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_opportunities_skips_blank_keywords() -> None:
    seeds = [KeywordSeed(keyword=""), KeywordSeed(keyword="   "), KeywordSeed(keyword="real topic")]
    ranked = rank_opportunities(seeds, [], [], data_sufficient=True)
    assert [o.keyword for o in ranked] == ["real topic"]
