"""Tests for lib.crew.products.selector + its lib.crew.writer.orchestrator wiring.

Same convention as test_crew_products_stage.py / test_crew_writer_orchestrator.py:
the CrewAI `kickoff()` call is never exercised -- a fake `execute_fn` (the
`SelectorExecuteFn` seam) captures the built task description and returns a
canned `ProductSelection` (or `None`, to simulate a failed LLM call). Both
catalog loaders read real files built in tmp_path. No real CrewAI/DeepSeek
network calls, no DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from crewai import Agent, Task

from lib.crew.products.models import ProductSelection, SelectedProduct
from lib.crew.products.selector import MAX_PRODUCTS, select_products_for_post
from lib.crew.products.usage import record_usage
from lib.crew.writer.models import ContentBrief, WrittenPost
from lib.crew.writer.orchestrator import write_post_from_brief

_DISCLOSURE = "Affiliate disclosure: Amazon affiliate links, purchases support this site."

_FLAT_CATALOG = [
    {
        "key": "gps-tracker",
        "asin": "B000000001",
        "display": "PawTrack GPS Collar",
        "category": "gear",
        "notes": "no-subscription tracker",
    },
    {"key": "run-harness", "asin": "B000000002", "display": "TrailBlazer Harness"},
    {"key": "treat-pouch", "asin": "B000000003", "display": "SnackPack Pouch"},
    {"key": "water-bottle", "asin": "B000000004", "display": "HydroHound Bottle"},
    {"key": "paw-balm", "asin": "B000000005", "display": "PawShield Balm"},
    {"key": "led-collar", "asin": "B000000006", "display": "GlowPup LED Collar"},
]

_RECIPE_CATALOG = {
    "categories": {
        "baking": [
            {
                "key": "silicone-mat",
                "asin": "B00ABCDE12",
                "display": "Silicone Baking Mat",
                "blurb": "Non-stick mat.",
            }
        ]
    },
    "recipe_type_map": {"_default": ["baking"]},
    "recipe_overrides": {},
}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_ambient_brand_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep `usage.py`'s BRAND_DIR-derived default path out of these tests --
    each test passes an explicit `usage_path` or sets its own BRAND_DIR."""
    monkeypatch.delenv("BRAND_DIR", raising=False)


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "config.json",
        {"site": {"name": "DogFoodAndFun", "brand_persona": "Nalla's Dad"}},
    )
    _write_json(tmp_path / "data" / "config" / "affiliate_products.json", _FLAT_CATALOG)
    return tmp_path


def _brief(**overrides: Any) -> ContentBrief:
    defaults: dict[str, Any] = {
        "suggested_title": "Best No-Subscription GPS Trackers 2026",
        "primary_keyword": "no subscription dog gps tracker",
        "mascot_angle": "Nalla wandered off during a trail run once.",
    }
    defaults.update(overrides)
    return ContentBrief(**defaults)


def _selection(*keys: str) -> ProductSelection:
    return ProductSelection(
        products=[SelectedProduct(key=key, reason=f"{key} fits the post.") for key in keys]
    )


class _FakeSelector:
    """Injected `SelectorExecuteFn`: records each built task description."""

    def __init__(self, selection: ProductSelection | None) -> None:
        self.selection = selection
        self.descriptions: list[str] = []

    def __call__(self, agent: Agent, task: Task) -> ProductSelection | None:
        self.descriptions.append(task.description)
        return self.selection


# ── select_products_for_post ─────────────────────────────────────────────────


def test_select_returns_only_picked_entries(brand_dir: Path) -> None:
    fake = _FakeSelector(_selection("gps-tracker", "run-harness"))
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake)
    assert list(result) == ["gps-tracker", "run-harness"]
    assert result["gps-tracker"].display == "PawTrack GPS Collar"
    assert result["run-harness"].asin == "B000000002"


def test_select_caps_at_max_products_preserving_selection_order(brand_dir: Path) -> None:
    assert MAX_PRODUCTS == 4
    fake = _FakeSelector(
        _selection("led-collar", "gps-tracker", "paw-balm", "treat-pouch", "water-bottle")
    )
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake)
    assert list(result) == ["led-collar", "gps-tracker", "paw-balm", "treat-pouch"]


def test_select_drops_unknown_key_but_keeps_real_picks(brand_dir: Path) -> None:
    fake = _FakeSelector(_selection("invented-gadget", "gps-tracker"))
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake)
    assert list(result) == ["gps-tracker"]


def test_select_none_falls_back_to_flat_catalog_unchanged(brand_dir: Path) -> None:
    """A selector failure must reproduce exactly today's pre-selector
    behavior: the FLAT brand catalog, without the recipe-catalog entries the
    merged pool would add."""
    _write_json(brand_dir / "data" / "config" / "recipe_products.json", _RECIPE_CATALOG)
    fake = _FakeSelector(None)
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake)
    assert set(result) == {entry["key"] for entry in _FLAT_CATALOG}
    assert "silicone-mat" not in result
    # The recipe key WAS offered as a candidate -- only the fallback is flat.
    assert "silicone-mat | Silicone Baking Mat" in fake.descriptions[0]


def test_select_empty_selection_returns_empty_dict(brand_dir: Path) -> None:
    fake = _FakeSelector(ProductSelection())
    assert select_products_for_post(brand_dir, _brief(), execute_fn=fake) == {}
    assert len(fake.descriptions) == 1  # the agent WAS consulted and declined


def test_select_empty_pool_returns_empty_without_consulting_agent(tmp_path: Path) -> None:
    fake = _FakeSelector(_selection("gps-tracker"))
    assert select_products_for_post(tmp_path, _brief(), execute_fn=fake) == {}
    assert fake.descriptions == []


def test_select_excludes_recently_used_keys_from_candidates(
    brand_dir: Path, tmp_path: Path
) -> None:
    usage_path = tmp_path / "state" / "product_usage.jsonl"
    record_usage("earlier-post", ["gps-tracker", "led-collar"], path=usage_path)
    fake = _FakeSelector(_selection("run-harness"))
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake, usage_path=usage_path)
    description = fake.descriptions[0]
    assert "gps-tracker | PawTrack GPS Collar" not in description
    assert "led-collar | GlowPup LED Collar" not in description
    assert "run-harness | TrailBlazer Harness" in description
    assert list(result) == ["run-harness"]


def test_select_relaxes_dedupe_when_exclusion_leaves_too_few(tmp_path: Path) -> None:
    brand_dir = tmp_path / "brand"
    _write_json(brand_dir / "data" / "config" / "affiliate_products.json", _FLAT_CATALOG[:3])
    usage_path = tmp_path / "product_usage.jsonl"
    record_usage("earlier-post", ["gps-tracker"], path=usage_path)
    fake = _FakeSelector(_selection("gps-tracker"))
    result = select_products_for_post(brand_dir, _brief(), execute_fn=fake, usage_path=usage_path)
    # 2 unused candidates < MAX_PRODUCTS -> the full pool is offered again,
    # so the recently used key is a legal candidate AND a legal pick.
    assert "gps-tracker | PawTrack GPS Collar" in fake.descriptions[0]
    assert list(result) == ["gps-tracker"]


# ── orchestrator wiring (write_post_from_brief) ──────────────────────────────


class _FakeWriter:
    """Injected `WriterExecuteFn`: records the writer prompt it was given."""

    def __init__(self, post: WrittenPost) -> None:
        self.post = post
        self.descriptions: list[str] = []

    def __call__(self, agent: Agent, task: Task) -> WrittenPost:
        self.descriptions.append(task.description)
        return self.post


def _written_post(**overrides: Any) -> WrittenPost:
    defaults: dict[str, Any] = {
        "title": "Best No-Subscription GPS Trackers 2026",
        "body_html": f"<p>{_DISCLOSURE}</p><p>[AFFILIATE:gps-tracker]</p>",
        "word_count": 2800,
        "affiliate_keys_used": ["gps-tracker"],
    }
    defaults.update(overrides)
    return WrittenPost(**defaults)


def test_write_post_from_brief_feeds_writer_only_selected_products_and_records_usage(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    fake_selector = _FakeSelector(_selection("gps-tracker"))
    fake_writer = _FakeWriter(_written_post())

    post = write_post_from_brief(
        brand_dir, _brief(), execute_fn=fake_writer, product_execute_fn=fake_selector
    )

    assert post is not None
    writer_prompt = fake_writer.descriptions[0]
    assert "PawTrack GPS Collar" in writer_prompt
    for unselected in ("TrailBlazer Harness", "SnackPack Pouch", "GlowPup LED Collar"):
        assert unselected not in writer_prompt

    usage_file = brand_dir / "state" / "product_usage.jsonl"
    rows = [json.loads(line) for line in usage_file.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"ts": rows[0]["ts"], "idea_id": _brief().suggested_title, "keys": ["gps-tracker"]}
    ]


def test_write_post_from_brief_records_no_usage_when_no_keys_used(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    fake_selector = _FakeSelector(_selection("gps-tracker"))
    fake_writer = _FakeWriter(
        _written_post(body_html=f"<p>{_DISCLOSURE}</p>", affiliate_keys_used=[])
    )

    post = write_post_from_brief(
        brand_dir, _brief(), execute_fn=fake_writer, product_execute_fn=fake_selector
    )

    assert post is not None
    assert not (brand_dir / "state" / "product_usage.jsonl").exists()
