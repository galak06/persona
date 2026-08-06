"""Tests for lib.crew.writer.context's affiliate-catalog + disclosure loaders.

Split out of test_crew_writer_context.py (which was approaching the 300-line
file-size cap) -- same fixtures/posture, just the affiliate-catalog slice.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from lib.crew.writer.context import (
    catalog_summary_text,
    load_brand_affiliate_catalog,
    load_disclosure_text,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    _write(path, json.dumps(data))


# ── load_brand_affiliate_catalog / catalog_summary_text ─────────────────────


def test_load_brand_affiliate_catalog_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_brand_affiliate_catalog(tmp_path) == {}


def test_load_brand_affiliate_catalog_loads_real_shaped_file(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "config" / "affiliate_products.json",
        [
            {
                "_note": "catalog note embedded in first entry",
                "key": "fi-collar",
                "asin": "B0FH8HQS3V",
                "display": "Fi Series 3+ Smart Dog Collar",
                "category": "gps-tracker",
                "notes": "Main product of the GPS comparison post.",
            },
            {"key": "apple-airtag", "asin": "B0933BVK6T", "display": "Apple AirTag (4-pack)"},
        ],
    )
    catalog = load_brand_affiliate_catalog(tmp_path)
    assert set(catalog.keys()) == {"fi-collar", "apple-airtag"}
    assert catalog["fi-collar"].asin == "B0FH8HQS3V"
    assert catalog["fi-collar"].category == "gps-tracker"
    assert catalog["apple-airtag"].display == "Apple AirTag (4-pack)"


def test_load_brand_affiliate_catalog_malformed_json_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "config" / "affiliate_products.json", "{not valid json")
    assert load_brand_affiliate_catalog(tmp_path) == {}


def test_load_brand_affiliate_catalog_skips_entry_missing_asin(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "config" / "affiliate_products.json",
        [
            {"key": "no-asin", "display": "Missing ASIN"},
            {"key": "fi-collar", "asin": "B0FH8HQS3V", "display": "Fi Collar"},
        ],
    )
    catalog = load_brand_affiliate_catalog(tmp_path)
    assert set(catalog.keys()) == {"fi-collar"}


def test_load_brand_affiliate_catalog_skips_entry_missing_key(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "config" / "affiliate_products.json",
        [
            {"asin": "B0FH8HQS3V", "display": "No Key"},
            {"key": "fi-collar", "asin": "B0FH8HQS3V", "display": "Fi Collar"},
        ],
    )
    catalog = load_brand_affiliate_catalog(tmp_path)
    assert set(catalog.keys()) == {"fi-collar"}


def test_catalog_summary_text_empty_catalog_instructs_skip() -> None:
    text = catalog_summary_text({})
    assert "omit" in text.lower()


def test_catalog_summary_text_lists_real_entries(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "config" / "affiliate_products.json",
        [{"key": "fi-collar", "asin": "B0FH8HQS3V", "display": "Fi Collar", "category": "gps"}],
    )
    catalog = load_brand_affiliate_catalog(tmp_path)
    text = catalog_summary_text(catalog)
    assert "fi-collar" in text
    assert "Fi Collar" in text
    assert "gps" in text


# ── load_disclosure_text ─────────────────────────────────────────────────────


def test_load_disclosure_text_missing_file_returns_default(tmp_path: Path) -> None:
    text = load_disclosure_text(tmp_path)
    assert "affiliate disclosure" in text.lower()


def test_load_disclosure_text_uses_brand_real_text(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "data" / "config" / "content_rules.json",
        {"affiliate": {"disclosure_text": "Affiliate disclosure: real brand text."}},
    )
    assert load_disclosure_text(tmp_path) == "Affiliate disclosure: real brand text."


def test_load_disclosure_text_malformed_json_falls_back_to_default(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "config" / "content_rules.json", "{not valid json")
    text = load_disclosure_text(tmp_path)
    assert "affiliate disclosure" in text.lower()
