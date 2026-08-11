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
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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


def get_group_join_limit_per_week(default: int = 15) -> int:
    """Return `scripts/fb_group_scout.py`'s weekly join-request cap from the
    brand overlay.

    Reads `<BRAND_DIR>/brand.json` -> `group_discovery.join_limit_per_week`.
    Returns `default` if `BRAND_DIR` is unset, `brand.json` is missing or
    malformed, or the field is absent or not an int -- same fallback
    contract as `get_group_join_limit()`. `default=15` matches the
    documented scouting cadence (5/day, 15/week) for brands that don't
    customize it.
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
    limit = group_discovery.get("join_limit_per_week")
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


def load_brand_env_into_environ(
    brand_dir: Path | None = None, *, apply_secrets: bool = True
) -> int:
    """Merge `<brand_dir>/.env` into os.environ. Returns count loaded.

    The single-brand-per-process counterpart to `load_brand_env()`: host
    scripts, launchd jobs and the per-brand worker container each serve exactly
    one brand, and every consumer reads its platform credentials straight off
    `os.environ` (`os.environ["FB_PAGE_TOKEN"]`, ...). The pure-dict version is
    kept for the *shared*, brand-agnostic worker, which must never let one
    brand's secrets reach its own global environment.

    Call this BEFORE `load_local_env()`: both skip keys already present, so
    loading the brand file first makes it authoritative for brand-owned keys
    while the shared settings file still supplies engine-wide ones. An explicit
    `FB_PAGE_TOKEN=... python ...` invocation still wins over both.

    `brand_dir` defaults to `$BRAND_DIR`, falling back to `default_brand_dir()`
    so bootstrap works before BRAND_DIR has been sourced from settings.

    `apply_secrets` overlays this brand's encrypted credentials
    (`_apply_brand_secrets`) as a side effect, NOT counted in the return value,
    and deliberately OVERRIDING what was just loaded. Entrypoints that publish
    want it (the default). `lib.config`'s import-time bootstrap passes False:
    the overlay opens a DB connection, and `lib.config` is imported by nearly
    every module — wiring a Postgres round-trip (or, with the DB down, a
    ~10-second pool timeout) into `import lib.config` was measured, real, and
    unacceptable. Import must stay cheap; credentials are an entrypoint concern.
    """
    if brand_dir is None:
        raw = os.environ.get("BRAND_DIR")
        if raw:
            brand_dir = Path(raw)
        else:
            from lib.config import default_brand_dir

            brand_dir = default_brand_dir()

    loaded = 0
    for key, value in load_brand_env(brand_dir).items():
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1

    if apply_secrets:
        _apply_brand_secrets(brand_dir.name)
    return loaded


def _apply_brand_secrets(brand_id: str) -> int:
    """Overlay this brand's encrypted secrets (`lib.brand_secrets`) on top of
    the `.env` values just loaded, OVERRIDING them and any ambient value.

    The override is the point, and it inverts the skip-if-present rule above.
    That rule exists so `FB_PAGE_TOKEN=... python ...` can win, but it cannot
    tell a deliberate override from a stale variable a shell happened to
    export -- and a dead token from a deleted Meta app did exactly that,
    shadowing the correct credential in two `.env` files until every publish
    failed with `OAuthException 190`. A row in `brand_secrets` is an explicit
    statement of what this brand's credential IS, so it wins.

    Failure severity depends on what failed:

    * **No master key** -> debug, skip quietly. Pre-migration state; the `.env`
      values stand and that is correct.
    * **Key present but the overlay failed** (wrong key, table missing, DB
      down) -> WARNING. Secrets were configured and could not be applied, so
      the process is now running on `.env`/ambient fallbacks -- the exact
      silent-stale-credential regression this module exists to prevent must
      never hide at debug level.
    """
    try:
        from lib import brand_secrets

        if brand_secrets.resolve_master_key() is None:
            logger.debug(
                "no %s; brand secrets skipped for %s", brand_secrets.MASTER_KEY_ENV, brand_id
            )
            return 0
        return brand_secrets.load_into_environ(brand_id, override=True)
    except Exception as exc:  # wrong key, no table, DB down -- key WAS present
        logger.warning(
            "brand secrets NOT applied for %s (%s) -- running on .env/ambient "
            "credentials, which may be stale",
            brand_id,
            exc,
        )
        return 0


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
