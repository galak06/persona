"""Tests for `lib/brand_context.py` and `lib.config`'s lazy `settings`.

Supersedes `tests/test_brand_resolver.py`: `resolve_brand_dir()` became
`BrandContext.from_env()` / `.for_brand()`, and all six of that file's cases
are carried over below unchanged in intent.

The env-var paths need no infra and always run. The registry paths hit the
`brands` table via `BrandsRepository`, so they follow this suite's live-Postgres
skipif pattern (see `test_db.py`).

The laziness tests are the point of the module: before 2026-08-15, importing
`lib.config` resolved `BRAND_DIR` and merged `os.environ` as a side effect, and
that import-time merge is how the live database was wiped twice.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lib import db
from lib.brand_context import (
    BrandContext,
    BrandDirNotSetError,
    BrandNotFoundError,
    current_brand_id,
    default_brand_dir,
    resolve_paths,
)
from lib.brands_db.repository import BrandsRepository

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _REPO_ROOT / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """`lib.config` caches the parsed settings; these tests move BRAND_DIR."""
    from lib import config

    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


# --------------------------------------------------------------- from_env()


def test_from_env_reads_brand_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.delenv("PERSONA_BRAND", raising=False)
    ctx = BrandContext.from_env()
    assert ctx.brand_dir == tmp_path.resolve()
    assert ctx.brand_id == tmp_path.name


def test_from_env_raises_clear_error_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_DIR", raising=False)
    with pytest.raises(ValueError, match="BRAND_DIR"):
        BrandContext.from_env()


def test_from_env_raises_when_env_var_is_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_DIR", "")
    with pytest.raises(ValueError, match="BRAND_DIR"):
        BrandContext.from_env()


def test_persona_brand_wins_over_directory_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", "explicit-slug")
    assert BrandContext.from_env().brand_id == "explicit-slug"


def test_context_is_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A context is an answer, not a mutable setting."""
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    ctx = BrandContext.from_env()
    with pytest.raises(FrozenInstanceError):
        ctx.brand_id = "other"  # type: ignore[misc]


# ------------------------------------------------------------------- paths


def test_paths_derive_from_brand_dir(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    assert paths.brand_dir == tmp_path
    assert paths.dedup_cache == tmp_path / "state" / "dedup_cache.json"
    assert paths.logs_dir == tmp_path / "logs"
    assert paths.brand_voice_guide == tmp_path / "data" / "config" / "brand_voice_guide.md"


def test_context_paths_match_resolve_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    assert BrandContext.from_env().paths == resolve_paths(tmp_path.resolve())


# --------------------------------------------------------- current_brand_id


def test_current_brand_id_prefers_persona_brand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_BRAND", "from-slug")
    monkeypatch.setenv("BRAND_DIR", "/brands/from-dir")
    assert current_brand_id() == "from-slug"


def test_current_brand_id_falls_back_to_dir_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONA_BRAND", raising=False)
    monkeypatch.setenv("BRAND_DIR", "/brands/from-dir")
    assert current_brand_id() == "from-dir"


def test_current_brand_id_defaults_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONA_BRAND", raising=False)
    monkeypatch.delenv("BRAND_DIR", raising=False)
    assert current_brand_id() == "default"


def test_default_brand_dir_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some callers evaluate this at import time, so it must not raise."""
    monkeypatch.delenv("PERSONA_BRAND", raising=False)
    monkeypatch.delenv("BRAND_DIR", raising=False)
    assert isinstance(default_brand_dir(), Path)


# ------------------------------------------------------ laziness (the point)


def test_importing_lib_config_needs_no_brand_and_touches_no_env() -> None:
    """`import lib.config` must not resolve a brand or mutate os.environ.

    Run in a child process because this suite has already imported `lib.config`
    (and, deliberately, pinned DATABASE_URL). The assertion is the regression
    guard for the 2026-07-28 / 2026-08-12 database wipes: the import-time
    `load_brand_env_into_environ()` + `load_local_env()` merge fired during
    pytest collection, behind conftest's guard.
    """
    script = (
        "import os;"
        "before = set(os.environ);"
        "import lib.config;"
        "added = set(os.environ) - before;"
        "assert lib.config._cached is None, 'settings resolved at import';"
        "assert not added, f'import mutated os.environ: {sorted(added)}';"
        "print('ok')"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("BRAND_DIR", "PERSONA_BRAND")}
    env["PYTHONPATH"] = str(_REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_settings_resolves_on_first_attribute_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lib import config

    brand_dir = tmp_path / "acme"
    (brand_dir / "data" / "config").mkdir(parents=True)
    source = _REPO_ROOT / "brands" / "dogfoodandfun" / "config.json"
    if not source.exists():  # pragma: no cover - depends on local provisioning
        pytest.skip("no brand config.json available to copy")
    (brand_dir / "config.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    monkeypatch.setenv("PERSONA_BRAND", "acme")

    assert config._cached is None, "binding the proxy must not resolve"
    assert config.settings.paths is not None
    assert config._cached is not None, "first attribute access must resolve"
    assert config.settings.paths.brand_dir == brand_dir.resolve()


def test_settings_reports_missing_brand_dir_on_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import config

    monkeypatch.delenv("BRAND_DIR", raising=False)
    with pytest.raises(ValueError, match="BRAND_DIR"):
        _ = config.settings.site


# --------------------------------------------------------------- for_brand()


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


@requires_postgres
def test_for_brand_resolves_registered_brand_dir(pg: None) -> None:
    repo = BrandsRepository()
    repo.create(
        brand_id="acme-dogs",
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
    )
    repo.set_brand_dir("acme-dogs", "/brands/acme-dogs")

    ctx = BrandContext.for_brand("acme-dogs")
    assert ctx.brand_dir == Path("/brands/acme-dogs")
    assert ctx.brand_id == "acme-dogs"


@requires_postgres
def test_for_brand_keeps_its_own_id_regardless_of_env(
    pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`for_brand` must not inherit the process's PERSONA_BRAND.

    This is what keeps the interface open to serving more than one brand: an
    explicitly-requested brand always names itself.
    """
    monkeypatch.setenv("PERSONA_BRAND", "the-process-brand")
    repo = BrandsRepository()
    repo.create(
        brand_id="other-brand",
        name="Other",
        site_url="https://other.example",
        niche="n",
    )
    repo.set_brand_dir("other-brand", "/brands/other-brand")

    assert BrandContext.for_brand("other-brand").brand_id == "other-brand"


@requires_postgres
def test_for_brand_missing_brand_raises_brand_not_found(pg: None) -> None:
    with pytest.raises(BrandNotFoundError, match="no-such-brand"):
        BrandContext.for_brand("no-such-brand")


@requires_postgres
def test_for_brand_without_brand_dir_raises(pg: None) -> None:
    repo = BrandsRepository()
    repo.create(
        brand_id="not-provisioned",
        name="Not Provisioned",
        site_url="https://np.example",
        niche="n",
    )
    with pytest.raises(BrandDirNotSetError, match="not-provisioned"):
        BrandContext.for_brand("not-provisioned")
