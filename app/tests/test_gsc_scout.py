"""Tests for lib.gsc_scout -- brand-context loaders + the run_scout orchestrator.

`run_scout` is exercised entirely with injected fakes (`repo`,
`insert_idea_fn`, `existing_topics_fn`) -- no real Postgres, no real
Supabase. Never sets DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.gsc_scout import (
    load_keyword_seeds,
    load_site_content_cache,
    run_scout,
)
from lib.gsc_scout_scoring import GscOpportunity


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "config.json",
        {
            "site": {"name": "DogFoodAndFun", "url": "https://dogfoodandfun.com"},
            "content_analysis": {
                "keywords": {
                    "primary_keywords": ["dog food", "homemade"],
                    "competitor_mentions": ["some-competitor"],
                }
            },
        },
    )
    return tmp_path


class _FakeRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_for_brand(self, brand_id: str, *, limit: int = 10_000) -> list[dict[str, Any]]:
        return self._rows


def test_load_site_content_cache_prefers_current_reorg_path(brand_dir: Path) -> None:
    _write_json(
        brand_dir / "data" / "cache" / "site_content_cache.json", {"recent_posts": ["current"]}
    )
    _write_json(brand_dir / "data" / "site_content_cache.json", {"recent_posts": ["legacy"]})
    cache = load_site_content_cache(brand_dir)
    assert cache["recent_posts"] == ["current"]


def test_load_site_content_cache_falls_back_to_legacy_path(brand_dir: Path) -> None:
    _write_json(brand_dir / "data" / "site_content_cache.json", {"recent_posts": ["legacy"]})
    cache = load_site_content_cache(brand_dir)
    assert cache["recent_posts"] == ["legacy"]


def test_load_site_content_cache_missing_returns_empty(brand_dir: Path) -> None:
    assert load_site_content_cache(brand_dir) == {}


def test_load_keyword_seeds_excludes_competitor_category(brand_dir: Path) -> None:
    seeds = load_keyword_seeds(brand_dir)
    keywords = {s.keyword for s in seeds}
    assert "dog food" in keywords
    assert "homemade" in keywords
    assert "some-competitor" not in keywords


def test_load_keyword_seeds_ignores_keyword_clusters_json_even_if_present(
    brand_dir: Path,
) -> None:
    """keyword_clusters.json is retired repo-wide (static, manually-regenerated,
    no freshness guarantee) -- seeds come only from the brand's own live
    content_analysis.keywords now. A stale/malformed cluster file sitting on
    disk must not affect seed loading at all."""
    _write_json(
        brand_dir / "data" / "config" / "keyword_clusters.json",
        {"clusters": [{"pillar": {"keyword": "should never appear", "status": "PILLAR_GAP"}}]},
    )
    seeds = load_keyword_seeds(brand_dir)
    keywords = {s.keyword for s in seeds}
    assert "should never appear" not in keywords
    assert keywords == {"dog food", "homemade"}


def test_run_scout_inserts_scored_ideas_with_expected_shape(brand_dir: Path) -> None:
    repo = _FakeRepo(
        [
            {
                "gsc_query": "dog food",
                "position": 12.0,
                "impressions": 100,
                "wp_url": "https://dogfoodandfun.com/x/",
            }
        ]
    )
    inserted: list[dict[str, Any]] = []

    def fake_insert_idea(idea: dict[str, Any], *, brand_id: str, brand_name: str) -> str:
        inserted.append({"idea": idea, "brand_id": brand_id, "brand_name": brand_name})
        return f"idea-{len(inserted)}"

    results = run_scout(
        brand_dir,
        repo=repo,  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        dry_run=False,
    )

    assert len(results) >= 1
    assert all(idea_id is not None for _, idea_id in results)
    assert inserted, "insert_idea_fn should have been called"

    row = inserted[0]
    assert row["brand_id"] == brand_dir.name
    assert row["brand_name"] == "DogFoodAndFun"
    idea = row["idea"]
    assert set(idea.keys()) == {
        "category",
        "topic",
        "target_keyword",
        "nalla_context",  # ideas_db.insert_idea's real dict key -- see lib/gsc_scout.py's note
        "post_goal",
        "status",
        "input",
    }
    assert idea["status"] == "publish"
    assert isinstance(idea["target_keyword"], str) and idea["target_keyword"]
    parsed_input = json.loads(idea["input"])
    assert parsed_input["source"] == "gsc_scout"


def test_run_scout_skips_existing_topics(brand_dir: Path) -> None:
    repo = _FakeRepo([])
    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    results = run_scout(
        brand_dir,
        repo=repo,  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: {"dog food", "homemade"},
        dry_run=False,
    )
    assert not calls
    assert all(idea_id is None for _, idea_id in results)


def test_run_scout_dry_run_never_calls_insert_idea(brand_dir: Path) -> None:
    repo = _FakeRepo([])
    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "unexpected"

    results = run_scout(
        brand_dir,
        repo=repo,  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        dry_run=True,
    )
    assert not calls
    assert results  # still scores + returns opportunities
    assert all(idea_id is None for _, idea_id in results)


def test_run_scout_flags_insufficient_data_in_reason(brand_dir: Path) -> None:
    repo = _FakeRepo([])  # empty published_content -> definitely insufficient
    results = run_scout(
        repo=repo,  # type: ignore[arg-type]
        brand_dir=brand_dir,
        insert_idea_fn=lambda idea, **_: "id",
        existing_topics_fn=lambda **_: set(),
        dry_run=False,
    )
    assert results
    opportunity: GscOpportunity = results[0][0]
    assert opportunity.opportunity_type == "discovery"
    assert "insufficient GSC history" in opportunity.reason
