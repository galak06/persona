"""`persona_context` read-compatibility in `api/ideas_api.py::list_ideas`.

The `nalla_context` -> `persona_context` rename was done ADDITIVELY: the new
column was added and backfilled, the old one was never dropped
(`db/schema.sql`). `ideas_db.list_ideas` issues `SELECT *`, so the row dict
carries whichever columns the database it hit actually has. Three shapes are
therefore reachable at once during a rollout, and the API must read all
three the same way:

  * new column populated (a migrated database, the normal case)
  * only the legacy column present (a database the migration has not reached,
    or a row an older container wrote after a rollback)
  * both present, new one winning (the migrated steady state)

Same convention as `test_ideas_api_reel_transitions.py`: `lib.ideas_db`
faked at the module level, no live Postgres and no FastAPI TestClient --
`list_ideas` is a plain function. Its filter arguments must be passed
explicitly as None, though: outside a request the defaults are unresolved
`Query(None)` objects, and a `Query` object is truthy, so `list_ideas()`
would take the `if category:` branch and filter every row away.
"""

from __future__ import annotations

from typing import Any

import pytest
from api import ideas_api


def _row(**overrides: Any) -> dict[str, Any]:
    """One `SELECT *` row, minus whichever context column the test omits."""
    row: dict[str, Any] = {
        "id": "idea-1",
        "category": "health",
        "topic": "Winter coats",
        "status": "publish",
    }
    row.update(overrides)
    return row


@pytest.fixture
def rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Whatever this list holds is what `list_ideas` sees from the DB."""
    out: list[dict[str, Any]] = []
    # Dotted-string target rather than `ideas_api.ideas_db`: same patch, but
    # mypy rejects the attribute form (`api.ideas_api` re-exports `ideas_db`
    # only implicitly).
    monkeypatch.setattr("api.ideas_api.ideas_db.list_ideas", lambda **_kwargs: out)
    return out


def test_reads_new_column(rows: list[dict[str, Any]]) -> None:
    rows.append(_row(persona_context="from the new column"))
    resp = ideas_api.list_ideas(category=None, status=None, brand_id=None, limit=200)
    assert resp.ideas[0].persona_context == "from the new column"


def test_falls_back_to_legacy_column_when_new_one_is_absent(
    rows: list[dict[str, Any]],
) -> None:
    """An un-migrated database has no `persona_context` key at all."""
    rows.append(_row(nalla_context="from the legacy column"))
    resp = ideas_api.list_ideas(category=None, status=None, brand_id=None, limit=200)
    assert resp.ideas[0].persona_context == "from the legacy column"


def test_falls_back_to_legacy_column_when_new_one_is_null(
    rows: list[dict[str, Any]],
) -> None:
    """A row written by a rolled-back container: column exists, value doesn't."""
    rows.append(_row(persona_context=None, nalla_context="from the legacy column"))
    resp = ideas_api.list_ideas(category=None, status=None, brand_id=None, limit=200)
    assert resp.ideas[0].persona_context == "from the legacy column"


def test_new_column_wins_when_both_are_populated(rows: list[dict[str, Any]]) -> None:
    rows.append(_row(persona_context="new", nalla_context="stale"))
    resp = ideas_api.list_ideas(category=None, status=None, brand_id=None, limit=200)
    assert resp.ideas[0].persona_context == "new"


def test_absent_from_both_is_none_not_an_error(rows: list[dict[str, Any]]) -> None:
    rows.append(_row())
    resp = ideas_api.list_ideas(category=None, status=None, brand_id=None, limit=200)
    assert resp.ideas[0].persona_context is None
