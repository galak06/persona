"""Suite-wide safety: the tests can only ever touch a database they own.

Twenty-four test modules TRUNCATE shared tables on fixture teardown::

    db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")

Nothing distinguished the target database, so pointing `DATABASE_URL` at the
live Postgres and running the suite silently destroyed brand registration, the
schedule, the FB groups table and the publish history. That happened on
2026-07-28 while following the project's own documented "run pytest with
DATABASE_URL=..." instruction, and again on 2026-08-12 through a different
import path.

The fix is structural rather than advisory. This module no longer inspects the
supplied DSN and refuse when it looks dangerous -- it **derives** one it owns,
appending `_test` to the database name and creating that database if absent.
`postgresql://.../persona` becomes `postgresql://.../persona_test`. There is
therefore no supplied value that can aim the TRUNCATEs at production, and the
two escape hatches the old heuristic needed (`CI=1`, `PERSONA_ALLOW_PROD_DB=1`)
are gone with it -- both existed only to permit the dangerous case, and CI,
whose service database is literally named `persona`, depended on the first.

An unset or unreachable DATABASE_URL degrades to a run with no database, where
the pg-gated modules skip themselves. `pytest_report_header` states which of
these happened, so a silently-degraded run is visible rather than looking like
a clean pass.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

_TEST_DB_SUFFIX = "_test"
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

_DSN_VAR = "DATABASE_URL"
_startup_dsn = ""
_derivation_note = ""


def _database_name(dsn: str) -> str:
    """Database name from a libpq URL, or '' when it has none."""
    return urlparse(dsn).path.lstrip("/")


def _with_database(dsn: str, name: str) -> str:
    return urlunparse(urlparse(dsn)._replace(path=f"/{name}"))


def derive_test_dsn(dsn: str) -> str:
    """Same server, same credentials, a database this suite OWNS.

    The suite TRUNCATEs; it must therefore never be pointed at a database it
    did not create. Rather than asking the operator to supply a safe name and
    refusing when they don't, the name is derived here: `persona` becomes
    `persona_test`. A DSN already naming a `_test` database is passed through.

    This replaces a disposability heuristic that trusted the supplied value and
    carried two escape hatches -- `CI=1` and `PERSONA_ALLOW_PROD_DB=1` -- both
    of which existed only to permit the dangerous case. CI's own service
    database is literally named `persona`, so it depended entirely on the first
    hatch. Deriving the name removes the question.
    """
    name = _database_name(dsn)
    if not name:
        return ""
    if name.endswith(_TEST_DB_SUFFIX):
        return dsn
    return _with_database(dsn, f"{name}{_TEST_DB_SUFFIX}")


def _ensure_database(dsn: str) -> None:
    """CREATE DATABASE if absent, then apply `db/schema.sql`.

    Connects to the server's own `postgres` database to issue CREATE, which
    cannot run inside a transaction -- hence autocommit.
    """
    import psycopg

    name = _database_name(dsn)
    with psycopg.connect(_with_database(dsn, "postgres"), autocommit=True) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{name}"')

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))


def pytest_configure(config: pytest.Config) -> None:
    """Point the session at a derived, suite-owned test database.

    Runs before collection, so no fixture -- and therefore no TRUNCATE -- can
    execute against anything else. An unset DATABASE_URL stays unset: the DB
    tests skip themselves, which is the documented degraded-but-safe run.

    Also OCCUPIES the variable for the rest of the session. `app/.env` carries
    the live DSN and python-dotenv loads it when CrewAI is imported; that
    injector skips a key already present in `os.environ`, so holding the slot
    stops it from reaching a test process.

    Occupying is what makes this safe; resetting the value later is not enough.
    `_PG_AVAILABLE` is computed at module import (i.e. during collection) and
    opens the shared pool, which caches the DSN it saw. A DSN injected during
    collection therefore binds the pool to whatever it names, and the fixtures'
    TRUNCATEs follow that cached pool no matter what `os.environ` says by the
    time tests run. That is how the live database was wiped a second time on
    2026-08-12.
    """
    global _startup_dsn, _derivation_note
    supplied = os.environ.get(_DSN_VAR, "")

    if not supplied:
        _startup_dsn = ""
        os.environ[_DSN_VAR] = ""
        return

    derived = derive_test_dsn(supplied)
    if not derived:
        _derivation_note = f"{_DSN_VAR} names no database; running without one"
        _startup_dsn = ""
        os.environ[_DSN_VAR] = ""
        return

    try:
        _ensure_database(derived)
    except Exception as exc:  # server down, no CREATEDB right, bad credentials
        _derivation_note = (
            f"could not prepare {_database_name(derived)!r} ({exc}); running without a database"
        )
        _startup_dsn = ""
        os.environ[_DSN_VAR] = ""
        return

    if derived != supplied:
        _derivation_note = (
            f"redirected {_database_name(supplied)!r} -> {_database_name(derived)!r} "
            "(this suite TRUNCATEs; it only ever touches a database it owns)"
        )
    _startup_dsn = derived
    os.environ[_DSN_VAR] = derived


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every database-backed test `integration`, without touching 20 files.

    A module that imports `tests._pg` binds `requires_postgres` at module
    scope; that is exactly the set of modules which TRUNCATE. Deriving the
    marker from that import keeps the two in sync automatically — a new
    database-backed module gets marked the moment it uses the shared gate.

    The point is being able to say `-m "not integration"` and mean it.
    Previously the only way to exclude these was to leave `DATABASE_URL`
    unset, which excluded them *silently* and looked identical to a pass.
    """
    for item in items:
        module = getattr(item, "module", None)
        if module is not None and hasattr(module, "requires_postgres"):
            item.add_marker("integration")


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Say which database the run is using, and why."""
    if _derivation_note:
        return [f"conftest: {_derivation_note}"]
    if _startup_dsn:
        return [f"conftest: database {_database_name(_startup_dsn)!r}"]
    return ["conftest: no DATABASE_URL — database-backed tests will skip"]


def _restore_startup_dsn(when: str) -> str | None:
    """Force DATABASE_URL back to whatever the invoking command supplied.

    The suite must target exactly the database named on the command line --
    never one an import happened to introduce. Two independent import paths
    injected the live DSN on a developer machine:

      * `lib/config.py`'s import-time `load_local_env()` merging
        `.claude/settings.local.json`. **Closed at source on 2026-08-15**: that
        module-level bootstrap moved behind `BrandContext.load_env()`, so
        importing `lib.config` no longer touches `os.environ` at all. (It was
        already refused for DSN keys by `lib.local_env._is_db_key`.)
      * `app/.env`, auto-loaded by python-dotenv when CrewAI is imported
        (`lib.crew` -> `crewai`). That file is also docker-compose's env file,
        so the DSN cannot simply be deleted from it.

    **The second path is still live and is why this pin remains.** `app/.env`
    carries the real `.../persona` DSN, `crewai` imports `dotenv`, and none of
    that is under this repo's control -- removing the pin because the first
    injector is gone would reopen the hole that wiped the live database on
    2026-08-12. Pinning closes the whole class of injection regardless of
    mechanism, instead of chasing each new import that reintroduces it.

    Returns the DSN that was displaced, or None when nothing changed.
    """
    displaced = os.environ.get(_DSN_VAR, "")
    if displaced == _startup_dsn:
        return None
    os.environ[_DSN_VAR] = _startup_dsn
    if displaced:
        print(
            f"\n[conftest] {_DSN_VAR} was injected {when} "
            f"(database {_database_name(displaced)!r}); reset to the "
            "invoking command's value. Tests never follow an injected DSN."
        )
    return displaced


@pytest.fixture(autouse=True)
def _pin_database_url() -> None:
    """Re-pin before every test, in case a lazy import injects mid-session."""
    _restore_startup_dsn("during the session")


def pytest_collection_finish(session: pytest.Session) -> None:
    """Undo any DSN injected by collection, before a single test executes.

    The configure-time check is defeated by import side effects. Collection
    imports test modules; anything one of them pulls in transitively can merge
    a live `DATABASE_URL` into `os.environ` mid-collection. Every pg-gated
    module imported after that point then computes its `requires_postgres`
    guard as True and the suite TRUNCATEs the live database. This exact
    sequence wiped the live docker `persona` DB on 2026-08-12 (recovered from
    the nightly dump).

    `lib/config.py` was one such importer and no longer is (see
    `_restore_startup_dsn`), but `app/.env` -> python-dotenv -> `crewai`
    remains, so this hook is still load-bearing. The only safe full-suite
    invocation on a machine with live credentials is an EXPLICIT disposable
    DSN, e.g.
    `DATABASE_URL=postgresql://persona:persona@localhost:5434/persona_test`.

    Resetting rather than aborting keeps the documented degraded-but-safe run
    (`env -u DATABASE_URL pytest`) usable: the pg-gated modules simply skip,
    which is what an unset DSN is supposed to mean. Only a DSN the *user*
    supplied can select a database, and `pytest_configure` already refused it
    if it was not disposable.
    """
    _restore_startup_dsn("during collection")


@pytest.fixture
def brand_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """A throwaway `BrandContext` over `tmp_path`, with the env pointed at it.

    The first domain fixture this file has offered. Tests that need "a brand"
    previously had to know the on-disk layout and monkeypatch eight attributes
    onto the live `lib.config.settings` singleton -- see
    `tests/lib/engagement/_env_builders.py`. Now that brand identity has an
    owning module, a test can construct one instead of patching a global.
    """
    from lib import config
    from lib.brand_context import BrandContext

    for sub in (
        "data/config",
        "data/trackers",
        "data/cache",
        "data/assets/reference_images",
        "state",
        "logs",
    ):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    # A brand without a config.json is not a brand `lib.config` can load, so
    # seed one from the engine's own template — the same file `onboard_brand`
    # copies. Tests get a fully-resolvable `settings`, not just a directory.
    template = Path(__file__).resolve().parent.parent / "config.example.json"
    (tmp_path / "config.json").write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", tmp_path.name)
    config.reset_settings_cache()
    try:
        yield BrandContext.from_env()
    finally:
        config.reset_settings_cache()
