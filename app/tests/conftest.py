"""Suite-wide safety guard: never let destructive tests hit a real database.

Twelve test modules TRUNCATE shared tables on fixture teardown::

    db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")

Nothing distinguished the target database, so pointing `DATABASE_URL` at the
live Postgres and running the suite silently destroyed brand registration, the
schedule, the FB groups table and the publish history. That happened on
2026-07-28 while following the project's own documented "run pytest with
DATABASE_URL=..." instruction, and there are no pg_dump backups to restore from.

This module refuses to start such a run. A database is considered disposable
when ANY of:

  * `CI` is set -- GitHub Actions provisions a throwaway postgres service
    container per job, so its `persona` database is safe by construction.
  * the database name ends with `_test` (e.g. `persona_test`) -- the intended
    local workflow.
  * `PERSONA_ALLOW_PROD_DB=1` -- explicit, deliberate override.

Anything else aborts the session before a single test runs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import ParseResult, urlparse

import pytest

_OVERRIDE_VAR = "PERSONA_ALLOW_PROD_DB"
_TEST_DB_SUFFIX = "_test"

_DSN_VAR = "DATABASE_URL"
_startup_dsn = ""


def _database_name(dsn: str) -> str:
    """Database name from a libpq URL, or '' when it has none."""
    return urlparse(dsn).path.lstrip("/")


def _is_disposable(dsn: str) -> bool:
    if os.environ.get("CI"):
        return True
    if os.environ.get(_OVERRIDE_VAR) == "1":
        return True
    return _database_name(dsn).endswith(_TEST_DB_SUFFIX)


def pytest_configure(config: pytest.Config) -> None:
    """Abort the session when DATABASE_URL names a non-disposable database.

    Runs before collection, so no fixture -- and therefore no TRUNCATE -- can
    execute against the wrong database. An unset DATABASE_URL is fine: the DB
    tests skip themselves, which is the documented degraded-but-safe run.

    Also OCCUPIES the variable for the rest of the session -- setting it to the
    empty string when the invoking command left it unset. Both known injectors
    (python-dotenv's `load_dotenv`, `lib.local_env`'s merge) skip a key that is
    already present in `os.environ`, so holding the slot stops the live DSN
    from ever entering a test process. `lib.db_pool._dsn` treats an empty value
    as unset and raises, which is exactly what the pg-gated modules probe for,
    so they skip -- the documented degraded-but-safe run.

    Occupying is what makes this safe; resetting the value later is not enough.
    `_PG_AVAILABLE` is computed at module import (i.e. during collection) and
    opens the shared pool, which caches the DSN it saw. A DSN injected during
    collection therefore binds the pool to the live database, and the fixtures'
    TRUNCATEs follow that cached pool no matter what `os.environ` says by the
    time tests run. That is how the live database was wiped a second time on
    2026-08-12 while this guard was being written.
    """
    global _startup_dsn
    _startup_dsn = os.environ.get(_DSN_VAR, "")

    if _startup_dsn and not _is_disposable(_startup_dsn):
        name = _database_name(_startup_dsn) or "<none>"
        raise pytest.UsageError(_refusal_message(name, urlparse(_startup_dsn)))

    os.environ[_DSN_VAR] = _startup_dsn


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

    for sub in ("data/config", "data/trackers", "data/cache", "state", "logs"):
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


def _refusal_message(name: str, parsed: ParseResult) -> str:
    return (
        f"\nRefusing to run: DATABASE_URL points at database {name!r} on "
        f"{parsed.hostname}:{parsed.port}, which is not disposable.\n"
        f"\nThis suite TRUNCATEs brands, schedule_tasks, fb_groups, engagements, "
        f"worker_runs and completed_tasks. Running it here would destroy live data.\n"
        f"\nCreate a throwaway database instead:\n"
        f"    createdb -h {parsed.hostname} -p {parsed.port} -U {parsed.username} {name}{_TEST_DB_SUFFIX}\n"
        f"    psql <dsn-for-{name}{_TEST_DB_SUFFIX}> -f db/schema.sql\n"
        f"then re-run with DATABASE_URL=...{name}{_TEST_DB_SUFFIX}\n"
        f"\nIf you genuinely mean to target this database, set {_OVERRIDE_VAR}=1.\n"
    )
