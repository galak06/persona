"""Tests for GET /api/v1/keywords -- the scout's search vocabulary viewer.

Exercised through the real FastAPI app over HTTP, so the route, the response
model, and the two libraries it joins (`lib.gsc_scout.load_keyword_seeds` and
`lib.crew.keyword_store`) are all covered together. Point of the endpoint: the
discovered half of the vocabulary grows on its own, and before this there was
no way to see it without reading a JSON file inside a container.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.crew import keyword_store
from lib.crew.trends.models import TrendSignal

_T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def _signal(keyword: str, *, score: float) -> TrendSignal:
    return TrendSignal(
        keyword=keyword,
        category="Health",
        opportunity_type="web_discovery",
        score=score,
        reason="rising forum discussion",
    )


@pytest.fixture
def brand_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A brand dir with two curated keywords, wired in as the active brand."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "site": {"name": "DogFoodAndFun"},
                "content_analysis": {
                    "keywords": {
                        "primary_keywords": ["dog food", "homemade"],
                        # Competitor terms are engagement-scan seeds, not
                        # candidate topics -- load_keyword_seeds drops them.
                        "competitor_mentions": ["some-competitor"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.keywords_api.resolve_api_brand", lambda: (tmp_path.name, str(tmp_path))
    )
    return tmp_path


def _get(brand_dir: Path) -> dict:
    from api.approval_api import app

    resp = TestClient(app).get("/api/v1/keywords")
    assert resp.status_code == 200
    return resp.json()


def test_returns_curated_keywords_excluding_competitor_terms(brand_dir: Path) -> None:
    body = _get(brand_dir)

    assert [k["keyword"] for k in body["curated"]] == ["dog food", "homemade"]
    assert body["discovered"] == []


def test_empty_store_is_not_an_error(brand_dir: Path) -> None:
    """A brand that has never run the scout is the normal state before the
    first Generate, not a 404."""
    body = _get(brand_dir)

    assert body["discovered"] == []
    assert body["active_limit"] == keyword_store.DEFAULT_ACTIVE_LIMIT


def test_discovered_keywords_carry_their_provenance(brand_dir: Path) -> None:
    keyword_store.record_signals(brand_dir, [_signal("gut health toppers", score=72.0)], now=_T0)

    entry = _get(brand_dir)["discovered"][0]

    assert entry["keyword"] == "gut health toppers"
    assert entry["best_score"] == 72.0
    assert entry["times_seen"] == 1
    assert entry["first_seen"] == _T0.isoformat()
    assert entry["reason"] == "rising forum discussion"
    assert entry["active"] is True


def test_terms_below_the_active_cut_are_returned_but_marked_inactive(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is additive and unbounded while the search vocabulary is
    capped, so a term can be on file and influencing nothing. Showing only
    the active ones would hide exactly what an operator needs to judge
    whether the cap is set right."""
    monkeypatch.setattr(keyword_store, "DEFAULT_ACTIVE_LIMIT", 1)
    keyword_store.record_signals(
        brand_dir,
        [_signal("strong term", score=90.0), _signal("weak term", score=5.0)],
        now=_T0,
    )

    discovered = _get(brand_dir)["discovered"]

    # Active first, then by score -- the cut is visible at a glance.
    assert [(k["keyword"], k["active"]) for k in discovered] == [
        ("strong term", True),
        ("weak term", False),
    ]


def test_a_curated_term_rediscovered_is_not_double_counted_as_active(brand_dir: Path) -> None:
    """`active_seeds` excludes curated terms so they can't occupy one of the
    limited slots -- the endpoint must report the same, or the UI would imply
    the term is being added twice."""
    keyword_store.record_signals(brand_dir, [_signal("dog food", score=99.0)], now=_T0)

    discovered = _get(brand_dir)["discovered"]

    assert [k["keyword"] for k in discovered] == ["dog food"]
    assert discovered[0]["active"] is False
