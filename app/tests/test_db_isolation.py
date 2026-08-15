"""Guards on the rule that keeps this suite off production.

The suite TRUNCATEs shared tables in 24 modules. It destroyed the live
database on 2026-07-28 and again on 2026-08-12. Safety used to depend on the
operator supplying a DSN whose database name ended in `_test`, backed by a
heuristic with two escape hatches.

`conftest.derive_test_dsn` replaced that with a derivation: whatever DSN is
supplied, the suite runs against `<name>_test` on the same server, created if
absent. These tests pin that rule and the invariant it produces.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import derive_test_dsn

_PROD = "postgresql://persona:persona@localhost:5434/persona"


def test_a_production_dsn_is_redirected() -> None:
    """The exact invocation that wiped the database twice."""
    assert derive_test_dsn(_PROD).endswith("/persona_test")


def test_credentials_host_and_port_are_preserved() -> None:
    """Only the database name changes — same server, same credentials."""
    derived = derive_test_dsn(_PROD)
    assert derived == "postgresql://persona:persona@localhost:5434/persona_test"


def test_an_already_test_dsn_is_untouched() -> None:
    dsn = "postgresql://persona:persona@localhost:5434/persona_test"
    assert derive_test_dsn(dsn) == dsn


def test_double_suffixing_cannot_happen() -> None:
    """Deriving twice is a no-op, so a re-entrant call can't make persona_test_test."""
    once = derive_test_dsn(_PROD)
    assert derive_test_dsn(once) == once


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://persona:persona@localhost:5434/",
        "postgresql://persona:persona@localhost:5434",
    ],
)
def test_a_dsn_naming_no_database_is_refused(dsn: str) -> None:
    """Returning '' makes conftest degrade to a no-database run.

    Falling back to the supplied DSN here would hand the TRUNCATEs the
    server's default database.
    """
    assert derive_test_dsn(dsn) == ""


def test_arbitrary_database_names_still_get_the_suffix() -> None:
    assert derive_test_dsn("postgresql://u:p@h:1/anything").endswith("/anything_test")


def test_the_running_session_is_on_a_test_database() -> None:
    """The invariant, asserted against this very process.

    If `DATABASE_URL` is set at all during a test run, it must name a database
    this suite owns. An empty value is the documented degraded run.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("degraded run: no database configured")
    assert dsn.rsplit("/", 1)[-1].endswith("_test"), (
        f"session is pointed at {dsn.rsplit('/', 1)[-1]!r}, which this suite does not own"
    )
