"""Load secrets from .claude/settings.local.json into os.environ.

Why this exists: Claude Code injects the settings file's `env` dict into the
process when running interactively, but launchd cron jobs run plain python and
inherit nothing. Per project rule (memory: feedback_secrets_read_from_config),
secrets live only in settings.local.json — not inline in scripts, not in
launchd plists. Scripts call `load_local_env()` at startup to bridge the gap.

Existing values in os.environ are NOT overwritten, so a manual `FB_PAGE_TOKEN=...
python ...` invocation still wins.

Also exposes brand-overlay runtime helpers (e.g. `get_runtime_headless`) that
read `<BRAND_DIR>/brand.json` for env-specific Playwright flags.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / ".claude" / "settings.local.json"


def load_local_env(*, settings_file: Path | None = None) -> int:
    """Merge settings.local.json `env` into os.environ. Returns count loaded."""
    path = settings_file or _SETTINGS_FILE
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return 0
    env = data.get("env") or {}
    loaded = 0
    for k, v in env.items():
        if k in os.environ or v is None:
            continue
        os.environ[k] = str(v)
        loaded += 1
    return loaded


def get_runtime_headless() -> bool:
    """Return Playwright headless setting from the brand overlay.

    Checks the `PLAYWRIGHT_HEADLESS` environment variable first:
    - ``PLAYWRIGHT_HEADLESS=1`` or ``PLAYWRIGHT_HEADLESS=true`` (case-insensitive) → True
    - ``PLAYWRIGHT_HEADLESS=0`` or ``PLAYWRIGHT_HEADLESS=false`` (case-insensitive) → False

    If absent or empty, falls through to `<BRAND_DIR>/brand.json` ->
    `runtime.headless`. Defaults to True (production-safe) if `BRAND_DIR` is
    unset, `brand.json` is missing or malformed, or the `runtime.headless`
    field is not specified.

    Set to False in `brand.json` for local dev to see the browser window.
    """
    _env_headless = os.environ.get("PLAYWRIGHT_HEADLESS", "").strip().lower()
    if _env_headless in ("1", "true"):
        return True
    if _env_headless in ("0", "false"):
        return False

    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return True
    brand_path = Path(brand_dir) / "brand.json"
    if not brand_path.exists():
        return True
    try:
        data: Any = json.loads(brand_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        return True
    headless = runtime.get("headless")
    if not isinstance(headless, bool):
        return True
    return headless


def get_group_join_limit(default: int = 10) -> int:
    """Return `scripts/fb_group_scout.py`'s daily join-request cap from the
    brand overlay.

    Reads `<BRAND_DIR>/brand.json` -> `group_discovery.join_limit_per_day`.
    Returns `default` if `BRAND_DIR` is unset, `brand.json` is missing or
    malformed, or the field is absent or not an int -- same fallback
    contract as `get_runtime_headless()`.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return default
    brand_path = Path(brand_dir) / "brand.json"
    if not brand_path.exists():
        return default
    try:
        data: Any = json.loads(brand_path.read_text())
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(data, dict):
        return default
    group_discovery = data.get("group_discovery")
    if not isinstance(group_discovery, dict):
        return default
    limit = group_discovery.get("join_limit_per_day")
    if not isinstance(limit, int) or isinstance(limit, bool):
        return default
    return limit


def load_brand_env(brand_dir: Path) -> dict[str, str]:
    """Parse `<brand_dir>/.env` (plain `KEY=VALUE` lines) into a dict.

    Per CLAUDE.md's documented credential model, brand-specific platform
    secrets (FB_PAGE_TOKEN, WP_APP_PASSWORD, IG_ACCOUNT_ID, etc.) live in
    `$BRAND_DIR/.env`. Used by a shared, brand-agnostic worker to build a
    per-task subprocess environment -- deliberately does NOT touch
    `os.environ` itself, so one brand's secrets never leak into the shared
    worker process's own global environment; callers merge the returned
    dict into a per-subprocess `env=` argument instead.

    Returns `{}` if the file is missing. Blank lines and `#`-comments are
    skipped; malformed lines (no `=`) are skipped rather than raising.
    """
    env_path = brand_dir / ".env"
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def get_brand_campaign() -> dict[str, Any]:
    """Return the `campaign` block from the brand overlay (or {}).

    Mirrors the same `_brand_campaign` slot produced by
    `tools.profiles_build.merge_brand_into_profiles`, but read direct from
    `<BRAND_DIR>/brand.json` so callers (publishers, scripts) don't need
    the full profile-merge pipeline at import time.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return {}
    brand_path = Path(brand_dir) / "brand.json"
    if not brand_path.exists():
        return {}
    try:
        data: Any = json.loads(brand_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    campaign = data.get("campaign")
    return campaign if isinstance(campaign, dict) else {}
