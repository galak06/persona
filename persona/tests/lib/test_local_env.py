"""Unit tests for lib.local_env.get_runtime_headless.

Covers the brand-overlay-driven Playwright headless toggle. Default is
production-safe `True` whenever the overlay can't be read; only an explicit
`runtime.headless: false` in `<BRAND_DIR>/brand.json` flips it off.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.local_env import get_runtime_headless


def _write_brand(tmp_path: Path, payload: object) -> Path:
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    if isinstance(payload, str):
        # Malformed JSON: write raw bytes
        (brand_dir / "brand.json").write_text(payload)
    else:
        (brand_dir / "brand.json").write_text(json.dumps(payload))
    return brand_dir


def test_returns_true_when_brand_dir_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_DIR", raising=False)
    assert get_runtime_headless() is True


def test_returns_true_when_brand_json_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = tmp_path / "empty_brand"
    brand_dir.mkdir()
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True


def test_returns_true_when_no_runtime_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, {"brand": {"name": "X"}})
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True


def test_returns_false_when_runtime_headless_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, {"runtime": {"headless": False}})
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is False


def test_returns_true_when_runtime_headless_is_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, {"runtime": {"headless": True}})
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True


def test_returns_true_when_runtime_headless_is_invalid_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, {"runtime": {"headless": "yes"}})
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True


def test_returns_true_when_runtime_is_not_a_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, {"runtime": ["headless"]})
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True


def test_returns_true_when_brand_json_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brand_dir = _write_brand(tmp_path, "{not valid json")
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    assert get_runtime_headless() is True
