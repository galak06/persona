"""Tests for lib.crew.products.pool's merged candidate pool.

No mocking -- both catalog loaders read real files built in tmp_path,
same posture as test_crew_writer_affiliate_catalog.py. The recipe fixture
carries the full validated shape (`categories`, `recipe_type_map` with a
`_default`, 10-char ASINs, globally-unique keys) so `load_catalog`'s real
validation runs.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lib.crew.products.pool import load_candidate_pool

_FLAT_CATALOG = [
    {
        "key": "fi-collar",
        "asin": "B0FH8HQS3V",
        "display": "Fi Series 3+ Smart Dog Collar",
        "category": "gps-tracker",
        "notes": "Main product of the GPS comparison post.",
    },
    {"key": "apple-airtag", "asin": "B0933BVK6T", "display": "Apple AirTag (4-pack)"},
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
        ],
        "storage": [
            {
                "key": "treat-jar",
                "asin": "B00ZZZZ123",
                "display": "Treat Jar",
                "blurb": "Airtight jar.",
            }
        ],
    },
    "recipe_type_map": {"pumpkin": ["baking"], "_default": ["storage"]},
    "recipe_overrides": {},
}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_flat(brand_dir: Path, entries: list[dict[str, str]]) -> None:
    _write_json(brand_dir / "data" / "config" / "affiliate_products.json", entries)


def _write_recipe(brand_dir: Path, catalog: dict[str, object]) -> None:
    _write_json(brand_dir / "data" / "config" / "recipe_products.json", catalog)


def test_load_candidate_pool_merges_both_catalogs(tmp_path: Path) -> None:
    _write_flat(tmp_path, _FLAT_CATALOG)
    _write_recipe(tmp_path, _RECIPE_CATALOG)
    pool = load_candidate_pool(tmp_path)
    assert set(pool.keys()) == {"fi-collar", "apple-airtag", "silicone-mat", "treat-jar"}
    # Flat entries keep their own shape.
    assert pool["fi-collar"].asin == "B0FH8HQS3V"
    assert pool["fi-collar"].category == "gps-tracker"
    # Recipe entries convert: blurb -> notes, category preserved.
    assert pool["treat-jar"].asin == "B00ZZZZ123"
    assert pool["treat-jar"].category == "storage"
    assert pool["treat-jar"].notes == "Airtight jar."
    assert pool["silicone-mat"].display == "Silicone Baking Mat"


def test_load_candidate_pool_collision_keeps_flat_entry(tmp_path: Path) -> None:
    _write_flat(tmp_path, _FLAT_CATALOG)
    recipe = {
        "categories": {
            "gps": [
                {
                    "key": "fi-collar",
                    "asin": "B000000000",
                    "display": "Recipe-Side Fi Collar",
                    "blurb": "Should lose to the flat entry.",
                }
            ]
        },
        "recipe_type_map": {"_default": ["gps"]},
        "recipe_overrides": {},
    }
    _write_recipe(tmp_path, recipe)
    pool = load_candidate_pool(tmp_path)
    assert pool["fi-collar"].asin == "B0FH8HQS3V"
    assert pool["fi-collar"].display == "Fi Series 3+ Smart Dog Collar"
    assert pool["fi-collar"].notes == "Main product of the GPS comparison post."


def test_load_candidate_pool_missing_recipe_catalog_is_flat_only(tmp_path: Path) -> None:
    _write_flat(tmp_path, _FLAT_CATALOG)
    pool = load_candidate_pool(tmp_path)
    assert set(pool.keys()) == {"fi-collar", "apple-airtag"}


def test_load_candidate_pool_invalid_recipe_catalog_is_flat_only(tmp_path: Path) -> None:
    _write_flat(tmp_path, _FLAT_CATALOG)
    # Missing the required `_default` type-map fallback -> RecipeCatalogError.
    _write_recipe(tmp_path, {"categories": {}, "recipe_type_map": {}, "recipe_overrides": {}})
    pool = load_candidate_pool(tmp_path)
    assert set(pool.keys()) == {"fi-collar", "apple-airtag"}


def test_load_candidate_pool_both_missing_returns_empty(tmp_path: Path) -> None:
    assert load_candidate_pool(tmp_path) == {}


def _write_discovered(brand_dir: Path, entries: list[dict[str, str]]) -> None:
    _write_json(brand_dir / "data" / "cache" / "discovered_products.json", entries)


def test_load_candidate_pool_includes_discovered_products(tmp_path: Path) -> None:
    """The regression: the drafting path writes `[AFFILIATE:<discovered-key>]`
    placeholders (selector runs with `discover=True`), then resolves them
    against this pool. A pool that omits the discovery cache made every post
    on an uncurated topic die at assembly with AffiliateResolverError.
    """
    _write_flat(tmp_path, _FLAT_CATALOG)
    _write_discovered(
        tmp_path,
        [
            {
                "key": "dog-pulling-harness-b0dzhpyhgs",
                "asin": "B0DZHPYHGS",
                "display": "Dog Pulling Harness",
                "category": None,
                "notes": None,
                "ts": datetime.now(UTC).isoformat(),
            }
        ],
    )
    pool = load_candidate_pool(tmp_path)
    assert "dog-pulling-harness-b0dzhpyhgs" in pool
    assert pool["dog-pulling-harness-b0dzhpyhgs"].asin == "B0DZHPYHGS"


def test_load_candidate_pool_curated_entry_beats_discovered_one(tmp_path: Path) -> None:
    _write_flat(tmp_path, _FLAT_CATALOG)
    _write_discovered(
        tmp_path,
        [
            {
                "key": "fi-collar",
                "asin": "B000000000",
                "display": "SERP-Side Fi Collar",
                "category": None,
                "notes": None,
                "ts": datetime.now(UTC).isoformat(),
            }
        ],
    )
    pool = load_candidate_pool(tmp_path)
    assert pool["fi-collar"].asin == "B0FH8HQS3V"
    assert pool["fi-collar"].display == "Fi Series 3+ Smart Dog Collar"


def test_load_candidate_pool_ignores_expired_discoveries(tmp_path: Path) -> None:
    """Discovery's own 30-day TTL still governs: a stale cache entry must not
    resurrect a key the selector can no longer justify picking."""
    _write_flat(tmp_path, _FLAT_CATALOG)
    _write_discovered(
        tmp_path,
        [
            {
                "key": "stale-find-b0aaaaaaaa",
                "asin": "B0AAAAAAAA",
                "display": "Stale Find",
                "category": None,
                "notes": None,
                "ts": (datetime.now(UTC) - timedelta(days=31)).isoformat(),
            }
        ],
    )
    assert set(load_candidate_pool(tmp_path).keys()) == {"fi-collar", "apple-airtag"}
