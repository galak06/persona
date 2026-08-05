"""Tests for the brand `.env` skeleton written at provisioning time.

Pure filesystem + string rendering -- no Postgres, no BRAND_DIR.
"""

from __future__ import annotations

import stat
from pathlib import Path

from lib.brand_env_template import (
    BRAND_ENV_TEMPLATE,
    brand_env_keys,
    render_brand_env_stub,
    write_brand_env_stub,
)
from lib.local_env import load_brand_env

_ENGINE_ONLY = (
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "FB_APP_ID",
    "FB_APP_SECRET",
    "PINTEREST_APP_ID",
    "PINTEREST_APP_SECRET",
    "TIKTOK_PRODUCTION_CLIENT_KEY",
    "TIKTOK_PRODUCTION_CLIENT_SECRET",
    "TIKTOK_SANDBOX_CLIENT_KEY",
    "TIKTOK_SANDBOX_CLIENT_SECRET",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGSMITH_API_KEY",
    "PHOENIX_ENDPOINT",
    "BRAND_DIR",
)


def test_template_carries_no_engine_wide_secrets() -> None:
    keys = brand_env_keys()
    leaked = [k for k in _ENGINE_ONLY if k in keys]
    assert leaked == [], f"engine-wide keys leaked into the brand template: {leaked}"


def test_template_covers_the_account_level_credentials() -> None:
    keys = brand_env_keys()
    for expected in (
        "WP_APP_PASSWORD",
        "FB_PAGE_TOKEN",
        "IG_ACCOUNT_ID",
        "INSTAGRAM_PASSWORD",
        "PINTEREST_ACCESS_TOKEN",
    ):
        assert expected in keys


def test_key_names_are_unique_across_sections() -> None:
    keys = brand_env_keys()
    assert len(keys) == len(set(keys))


def test_stub_is_fully_commented_so_it_parses_as_empty(tmp_path: Path) -> None:
    """A freshly scaffolded brand has no *active* keys -- every line is a
    comment until an operator fills one in, so load_brand_env sees {}."""
    (tmp_path / ".env").write_text(render_brand_env_stub("acme"))
    assert load_brand_env(tmp_path) == {}


def test_stub_mentions_every_section_heading() -> None:
    rendered = render_brand_env_stub("acme")
    for section, _keys in BRAND_ENV_TEMPLATE:
        assert section in rendered


def test_write_creates_file_with_owner_only_permissions(tmp_path: Path) -> None:
    assert write_brand_env_stub(tmp_path) is True
    env_path = tmp_path / ".env"
    assert env_path.exists()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_write_never_clobbers_an_existing_file(tmp_path: Path) -> None:
    """Re-provisioning must not wipe a brand's real secrets."""
    (tmp_path / ".env").write_text("FB_PAGE_TOKEN=real-secret\n")
    assert write_brand_env_stub(tmp_path) is False
    assert load_brand_env(tmp_path) == {"FB_PAGE_TOKEN": "real-secret"}
