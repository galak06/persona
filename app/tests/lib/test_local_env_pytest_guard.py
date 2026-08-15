"""Regression tests for the 2026-08-12 live-Postgres wipe.

`settings.local.json` carries the production `DATABASE_URL`; `lib.config`
merges it into `os.environ` at import time; every test module imports
`lib.config` transitively. conftest's live-DB guard runs before collection, so
the injection landed behind it and the pg fixtures truncated the real tables.

`lib.local_env` now refuses to merge a DSN whenever pytest is loaded.

Tests use marker key names and explicit subprocess environments: the ambient
environment of a real run already holds this repo's true settings values, and
"key already present" would otherwise mask what is being asserted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import local_env

APP_DIR = Path(__file__).resolve().parents[2]
LIVE_DSN = "postgresql://persona:persona@localhost:5434/persona"
MARKER_KEY = "PERSONA_GUARD_MARKER"


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A settings.local.json carrying a DSN alongside an ordinary key."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(MARKER_KEY, raising=False)
    path = tmp_path / "settings.local.json"
    path.write_text(json.dumps({"env": {"DATABASE_URL": LIVE_DSN, MARKER_KEY: "abc123"}}))
    return path


def _clean_env() -> dict[str, str]:
    """The current environment minus any DSN, for subprocess isolation."""
    return {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=APP_DIR,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_dsn_is_not_merged_under_pytest(settings_file: Path) -> None:
    """The wipe mechanism: a DSN must never reach a test process's environ."""
    loaded = local_env.load_local_env(settings_file=settings_file)

    assert "DATABASE_URL" not in os.environ
    assert os.environ[MARKER_KEY] == "abc123"
    assert loaded == 1, "only the non-DSN key counts as loaded"


def test_explicit_test_dsn_survives(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disposable DSN passed on the command line is left untouched."""
    monkeypatch.setenv("DATABASE_URL", f"{LIVE_DSN}_test")

    local_env.load_local_env(settings_file=settings_file)

    assert os.environ["DATABASE_URL"].endswith("_test")


def test_dsn_merges_normally_outside_pytest(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production behaviour is unchanged: cron scripts still get their DSN."""
    monkeypatch.setattr(local_env, "_is_under_pytest", lambda: False)

    loaded = local_env.load_local_env(settings_file=settings_file)

    assert os.environ["DATABASE_URL"] == LIVE_DSN
    assert loaded == 2


def test_brand_env_dsn_is_also_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The brand `.env` path shares the guard, so it cannot reopen the hole."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(MARKER_KEY, raising=False)
    brand_dir = tmp_path / "somebrand"
    brand_dir.mkdir()
    (brand_dir / ".env").write_text(f"DATABASE_URL={LIVE_DSN}\n{MARKER_KEY}=42\n")

    local_env.load_brand_env_into_environ(brand_dir, apply_secrets=False)

    assert "DATABASE_URL" not in os.environ
    assert os.environ[MARKER_KEY] == "42"


@pytest.mark.parametrize("key", ["DATABASE_URL", "RECIPES_DATABASE_URL"])
def test_is_db_key(key: str) -> None:
    assert local_env._is_db_key(key)


@pytest.mark.parametrize("key", ["PEXELS_API_KEY", "BRAND_DIR", "DATABASE_URL_NOTE"])
def test_is_not_db_key(key: str) -> None:
    assert not local_env._is_db_key(key)


def test_importing_lib_config_injects_nothing_even_outside_pytest() -> None:
    """Importing `lib.config` must not merge env, pytest loaded or not.

    Replaces an earlier *control* case which asserted the opposite -- that
    outside pytest the import-time bootstrap DID supply the DSN, proving the
    pytest guard was what suppressed it. That bootstrap was removed on
    2026-08-15: `lib/config.py` no longer calls `load_brand_env_into_environ()`
    / `load_local_env()` at module scope, the merge moved behind
    `BrandContext.load_env()`, and `settings` resolves lazily.

    So the property is now unconditional and strictly stronger, and this test
    pins the stronger one: merely importing `lib.config` touches no environment
    variable at all. `lib.local_env`'s pytest DSN refusal remains as
    defence-in-depth for the paths that DO merge (entrypoints, and python-dotenv
    via CrewAI, which conftest still pins against).
    """
    result = _run_python(
        "import os; before = set(os.environ); import lib.config; "
        "print(sorted(set(os.environ) - before))"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_import_under_pytest_does_not_inject_dsn() -> None:
    """The incident path itself: importing lib.config with pytest loaded."""
    result = _run_python("import pytest, os, lib.config; print(os.environ.get('DATABASE_URL'))")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"
