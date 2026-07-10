"""Tests for `lib/brand_resolver.py` (the `resolve_brand_dir()` seam).

The `brand_id=None` path (env var) needs no infra and always runs. The
`brand_id=<id>` path hits the `brands` table via `BrandsRepository`, so those
cases follow the rest of this suite's live-Postgres skipif pattern (see
`test_db.py`) -- they run when reachable at `DATABASE_URL` and skip cleanly
otherwise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db
from lib.brand_resolver import (
    BrandDirNotSetError,
    BrandNotFoundError,
    resolve_brand_dir,
)
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


# ------------------------------------------------------------- brand_id=None (env var)


def test_none_reads_brand_dir_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_DIR", "/brands/dogfoodandfun")
    assert resolve_brand_dir() == Path("/brands/dogfoodandfun")
    assert resolve_brand_dir(None) == Path("/brands/dogfoodandfun")


def test_none_raises_clear_error_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_DIR", raising=False)
    with pytest.raises(ValueError, match="BRAND_DIR"):
        resolve_brand_dir()


def test_none_raises_when_env_var_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_DIR", "")
    with pytest.raises(ValueError, match="BRAND_DIR"):
        resolve_brand_dir()


# ------------------------------------------------------------ brand_id=<id> (registry)


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


@requires_postgres
def test_brand_id_resolves_registered_brand_dir(pg: None) -> None:
    repo = BrandsRepository()
    repo.create(
        brand_id="acme-dogs",
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
    )
    repo.set_brand_dir("acme-dogs", "/brands/acme-dogs")

    assert resolve_brand_dir("acme-dogs") == Path("/brands/acme-dogs")


@requires_postgres
def test_brand_id_missing_brand_raises_brand_not_found(pg: None) -> None:
    with pytest.raises(BrandNotFoundError, match="no-such-brand"):
        resolve_brand_dir("no-such-brand")


@requires_postgres
def test_brand_id_existing_brand_without_brand_dir_raises(pg: None) -> None:
    repo = BrandsRepository()
    repo.create(
        brand_id="not-provisioned",
        name="Not Provisioned",
        site_url="https://np.example",
        niche="n",
    )
    with pytest.raises(BrandDirNotSetError, match="not-provisioned"):
        resolve_brand_dir("not-provisioned")
