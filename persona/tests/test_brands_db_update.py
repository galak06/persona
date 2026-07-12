"""Tests for `BrandsRepository.update()` (split out of `test_brands_db.py`
to keep that file under the project's 300-line limit -- same fixture/skip
pattern, same live-Postgres integration style).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db
from lib.brands_db.repository import BrandsRepository

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def repo() -> Iterator[BrandsRepository]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield BrandsRepository()
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


@requires_postgres
def test_update_headless_only_leaves_other_fields_untouched(repo: BrandsRepository) -> None:
    repo.create(
        brand_id="updatable",
        name="Updatable",
        site_url="https://u.example",
        niche="n",
        keywords={"primary_keywords": ["kw1"]},
        competitor_accounts=["@rival"],
    )
    assert repo.update("updatable", headless=False) is True

    row = repo.get("updatable")
    assert row is not None
    assert row["headless"] is False
    assert row["keywords"] == {"primary_keywords": ["kw1"]}
    assert row["competitor_accounts"] == ["@rival"]


@requires_postgres
def test_update_keywords_only_leaves_headless_untouched(repo: BrandsRepository) -> None:
    repo.create(brand_id="kw-only", name="Kw Only", site_url="https://k.example", niche="n")
    assert repo.update("kw-only", keywords={"primary_keywords": ["new-kw"]}) is True

    row = repo.get("kw-only")
    assert row is not None
    assert row["keywords"] == {"primary_keywords": ["new-kw"]}
    assert row["headless"] is True  # untouched, still the create()-time default


@requires_postgres
def test_update_competitor_accounts_and_enabled_flows(repo: BrandsRepository) -> None:
    repo.create(brand_id="ca-only", name="Ca Only", site_url="https://c.example", niche="n")
    assert (
        repo.update("ca-only", competitor_accounts=["@new-rival"], enabled_flows=["ig-scanner"])
        is True
    )

    row = repo.get("ca-only")
    assert row is not None
    assert row["competitor_accounts"] == ["@new-rival"]
    assert row["enabled_flows"] == ["ig-scanner"]


@requires_postgres
def test_update_with_no_fields_returns_false_and_issues_no_query(repo: BrandsRepository) -> None:
    repo.create(brand_id="noop", name="Noop", site_url="https://n.example", niche="n")
    assert repo.update("noop") is False


@requires_postgres
def test_update_returns_false_for_missing_brand(repo: BrandsRepository) -> None:
    assert repo.update("no-such-brand", headless=False) is False


@requires_postgres
def test_update_does_not_leak_across_brands(repo: BrandsRepository) -> None:
    """Editing one brand's settings must never touch a sibling brand's row."""
    repo.create(brand_id="brand-a", name="Brand A", site_url="https://a.example", niche="n")
    repo.create(brand_id="brand-b", name="Brand B", site_url="https://b.example", niche="n")

    repo.update("brand-a", headless=False, keywords={"primary_keywords": ["a-kw"]})

    row_a = repo.get("brand-a")
    row_b = repo.get("brand-b")
    assert row_a is not None and row_b is not None
    assert row_a["headless"] is False
    assert row_a["keywords"] == {"primary_keywords": ["a-kw"]}
    assert row_b["headless"] is True
    assert row_b["keywords"] == {}
