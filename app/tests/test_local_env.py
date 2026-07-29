"""Tests for `lib/local_env.py::get_group_join_limit()` (PR6).

No Postgres needed -- pure filesystem + env var reads, isolated via
`tmp_path` + `monkeypatch.setenv`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.local_env import (
    get_group_join_limit,
    load_brand_env,
    load_brand_env_into_environ,
    load_local_env,
)


def _set_brand_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))


def test_defaults_when_brand_dir_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_DIR", raising=False)
    assert get_group_join_limit() == 10


def test_defaults_when_brand_json_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    assert get_group_join_limit() == 10


def test_defaults_when_brand_json_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    (tmp_path / "brand.json").write_text("not json", encoding="utf-8")
    assert get_group_join_limit() == 10


def test_defaults_when_group_discovery_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    (tmp_path / "brand.json").write_text(json.dumps({"runtime": {"headless": True}}))
    assert get_group_join_limit() == 10


def test_defaults_when_join_limit_not_an_int(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    (tmp_path / "brand.json").write_text(
        json.dumps({"group_discovery": {"join_limit_per_day": "five"}})
    )
    assert get_group_join_limit() == 10


def test_defaults_when_join_limit_is_a_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bool` is a subclass of `int` in Python -- must not be accepted as a limit."""
    _set_brand_dir(monkeypatch, tmp_path)
    (tmp_path / "brand.json").write_text(
        json.dumps({"group_discovery": {"join_limit_per_day": True}})
    )
    assert get_group_join_limit() == 10


def test_reads_custom_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    (tmp_path / "brand.json").write_text(json.dumps({"group_discovery": {"join_limit_per_day": 3}}))
    assert get_group_join_limit() == 3


def test_custom_default_param(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_brand_dir(monkeypatch, tmp_path)
    assert get_group_join_limit(default=25) == 25


# --------------------------------------------------------------------------- load_brand_env


def test_load_brand_env_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_brand_env(tmp_path) == {}


def test_load_brand_env_parses_key_value_lines(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FB_PAGE_TOKEN=abc123\nWP_APP_PASSWORD=secret\n")
    assert load_brand_env(tmp_path) == {"FB_PAGE_TOKEN": "abc123", "WP_APP_PASSWORD": "secret"}


def test_load_brand_env_skips_blank_lines_and_comments(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("\n# a comment\nFB_PAGE_TOKEN=abc123\n\n")
    assert load_brand_env(tmp_path) == {"FB_PAGE_TOKEN": "abc123"}


def test_load_brand_env_skips_malformed_lines_without_equals(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("not-a-valid-line\nFB_PAGE_TOKEN=abc123\n")
    assert load_brand_env(tmp_path) == {"FB_PAGE_TOKEN": "abc123"}


def test_load_brand_env_does_not_mutate_os_environ(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SOME_TEST_ONLY_VAR=leak-test\n")
    load_brand_env(tmp_path)
    assert "SOME_TEST_ONLY_VAR" not in os.environ


# ------------------------------------------------- load_brand_env_into_environ


def test_load_brand_env_into_environ_sets_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("BRAND_ONLY_TOKEN=from-brand\n")
    monkeypatch.delenv("BRAND_ONLY_TOKEN", raising=False)
    assert load_brand_env_into_environ(tmp_path) == 1
    assert os.environ["BRAND_ONLY_TOKEN"] == "from-brand"


def test_load_brand_env_into_environ_does_not_overwrite_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `FOO=... python ...` invocation still wins."""
    (tmp_path / ".env").write_text("BRAND_ONLY_TOKEN=from-brand\n")
    monkeypatch.setenv("BRAND_ONLY_TOKEN", "from-shell")
    assert load_brand_env_into_environ(tmp_path) == 0
    assert os.environ["BRAND_ONLY_TOKEN"] == "from-shell"


def test_load_brand_env_into_environ_defaults_to_brand_dir_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("BRAND_ONLY_TOKEN=via-brand-dir\n")
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.delenv("BRAND_ONLY_TOKEN", raising=False)
    assert load_brand_env_into_environ() == 1
    assert os.environ["BRAND_ONLY_TOKEN"] == "via-brand-dir"


def test_load_brand_env_into_environ_is_noop_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    assert load_brand_env_into_environ() == 0


def test_brand_env_wins_over_settings_file_when_loaded_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bootstrap contract in lib/config.py: brand first, engine second.

    Both loaders skip keys already present, so ordering alone makes the brand
    file authoritative for a key that appears in both.
    """
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / ".env").write_text("SHARED_KEY=from-brand\n")
    settings = tmp_path / "settings.local.json"
    settings.write_text(json.dumps({"env": {"SHARED_KEY": "from-engine"}}))

    monkeypatch.delenv("SHARED_KEY", raising=False)
    load_brand_env_into_environ(brand_dir)
    load_local_env(settings_file=settings)

    assert os.environ["SHARED_KEY"] == "from-brand"
