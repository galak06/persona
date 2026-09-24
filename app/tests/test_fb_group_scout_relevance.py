"""fb-group-scout relevance: off-target groups are never joined, and what the
scout searches for / where it may join comes from the brand dir, not the engine.

The bad names are real auto-joins the FB engager could never comment in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.group_discovery.relevance import (
    DEFAULT_EXCLUDE_TERMS,
    ScoutRules,
    drop_off_target,
    load_scout_rules,
    off_target_reason,
)
from lib.group_discovery.scoring import score_group

CI_BRAND = Path(__file__).parent / "fixtures" / "ci_brand"
US_CA = ScoutRules(target_countries=frozenset({"US", "CA"}))


def _card(name: str, description: str = "", url: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "url": url or f"https://www.facebook.com/groups/{abs(hash(name))}",
        "member_text": "5.2K members",
        "member_count": 5_200,
        "privacy": "private",
        "post_frequency": "10 posts a day",
    }


BAD_GROUPS = [
    ("BACOLOD OWNER BUY AND SELL MIXED BREED DOG/PUPPY", "marketplace"),
    ("Dog Owners of Pontevedra", "geo"),
    ("East Ruston Cottages dog-friendly holidays", "travel"),
    ("FCE - Spinal Stroke Dog Owner Support Group", "medical"),
    ("Dogs friendly hotels", "travel"),
    ("Garmin GPS Dog Tracking System Buy/Sell/Trade Group", "marketplace"),
    ("Bladder Problems with Dogs", "medical"),
    ("Dog Businesses (Recommendations & In Search Of)", "promotion"),
]
GOOD_GROUPS = [
    ("Homemade Dog Food Recipes USA", "Share your homemade dog food and treat recipes."),
    ("Raw Feeding Dogs Canada", "Raw feeding tips and nutrition for Canadian dog owners."),
    ("German Shepherd Dog Owners of New Mexico", "Training tips, food and trail hikes."),
]


@pytest.mark.parametrize(("name", "category"), BAD_GROUPS)
def test_each_real_bad_auto_join_is_rejected(name: str, category: str) -> None:
    card = _card(name)
    reason = off_target_reason(card, US_CA)
    assert reason is not None and reason.startswith(category), reason
    assert score_group(card, rules=US_CA) == 0


@pytest.mark.parametrize(("name", "description"), GOOD_GROUPS)
def test_on_topic_north_american_groups_pass(name: str, description: str) -> None:
    card = _card(name, description)
    assert off_target_reason(card, US_CA) is None
    assert score_group(card, rules=US_CA) >= 40  # scout MIN_SCORE


def test_geo_signal_in_description_rejects() -> None:
    card = _card("Dog Owners Community", "A friendly group for dog lovers in Spain.")
    assert off_target_reason(card, US_CA) == "geo: ES outside target market"


def test_no_target_market_means_no_geo_restriction() -> None:
    assert off_target_reason(_card("Dog Owners of Pontevedra"), ScoutRules()) is None


def test_brand_exclude_terms_extend_engine_defaults() -> None:
    rules = ScoutRules(exclude_terms={**DEFAULT_EXCLUDE_TERMS, "brand": ("cat",)})
    assert off_target_reason(_card("Cat and Dog Pals"), rules) == "brand: 'cat'"
    assert off_target_reason(_card("Dogs friendly hotels"), rules) == "travel: 'hotels'"


def test_queued_off_target_groups_are_dropped() -> None:
    queued = [_card(n) for n, _ in BAD_GROUPS] + [_card(n, d) for n, d in GOOD_GROUPS]
    kept = drop_off_target(queued, US_CA)
    assert [g["name"] for g in kept] == [n for n, _ in GOOD_GROUPS]


def test_ci_brand_queries_come_from_its_config_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_DIR", str(CI_BRAND))
    rules = load_scout_rules()
    assert rules.search_queries == ("keyword1", "keyword2", "keyword3")
    assert rules.target_countries == frozenset()


def test_other_brand_config_yields_its_own_queries_and_market(tmp_path: Path) -> None:
    (tmp_path / "brand.json").write_text(
        json.dumps(
            {
                "brand": {"target_market": "USA + Canada"},
                "group_scout": {
                    "search_queries": ["sourdough baking", "bread recipes", "sourdough baking"],
                    "exclude_terms": ["gluten free"],
                },
            }
        )
    )
    (tmp_path / "config.json").write_text((CI_BRAND / "config.json").read_text())
    rules = load_scout_rules(tmp_path)
    assert rules.search_queries == ("sourdough baking", "bread recipes")
    assert rules.target_countries == frozenset({"US", "CA"})
    assert rules.exclude_terms["brand"] == ("gluten free",)
    assert rules.search_queries != load_scout_rules(CI_BRAND).search_queries


def test_explicit_target_countries_override_target_market(tmp_path: Path) -> None:
    (tmp_path / "brand.json").write_text(
        json.dumps(
            {
                "brand": {"target_market": "USA + Canada"},
                "group_scout": {"target_countries": ["gb"]},
            }
        )
    )
    rules = load_scout_rules(tmp_path)
    assert rules.target_countries == frozenset({"GB"})
    assert off_target_reason(_card("London Dog Walkers"), rules) is None
    assert off_target_reason(_card("Dog Owners USA"), rules) == "geo: US outside target market"


def test_scout_candidate_selection_drops_off_target_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import fb_group_scout as scout

    cards = [_card(n) for n, _ in BAD_GROUPS] + [_card(n, d) for n, d in GOOD_GROUPS]
    monkeypatch.setattr(scout, "search_groups", lambda _page, _q: [dict(c) for c in cards])
    monkeypatch.setattr(scout, "pace_between_queries", lambda: None)

    found = scout.collect_candidates(object(), [("keyword", "dog food")], set(), US_CA)

    assert sorted(g["name"] for g in found.values()) == sorted(n for n, _ in GOOD_GROUPS)
    assert all(g["score"] >= scout.MIN_SCORE for g in found.values())
