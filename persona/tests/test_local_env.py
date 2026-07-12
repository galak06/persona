"""Tests for `lib/local_env.py::get_group_join_limit()` (PR6).

No Postgres needed -- pure filesystem + env var reads, isolated via
`tmp_path` + `monkeypatch.setenv`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.local_env import get_group_join_limit


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
