"""Build this process's Jev gates from the brand row (or decline to).

The modes live in the ``brands`` table (``jev_post_gate_ig`` /
``jev_post_gate_fb`` for the engagers, ``jev_group_gate`` for
fb-group-scout), not in config.json, because ``provision_brand()``
rewrites that file on every Brand Settings save.
"""

from __future__ import annotations

import os
from typing import Final

from lib import brands_db
from lib.brand_context import current_brand_id
from lib.decisions.engager_gate import JevPostGate
from lib.decisions.jev_client import api_key_configured, warn_missing_key_once
from lib.decisions.modes import MODE_OFF, GateMode, parse_mode
from lib.decisions.scout_gate import MODE_COLUMN as GROUP_MODE_COLUMN
from lib.decisions.scout_gate import JevGroupGate
from lib.observability import get_logger

log = get_logger(__name__)

MODE_COLUMNS: Final[dict[str, str]] = {
    "instagram": "jev_post_gate_ig",
    "facebook": "jev_post_gate_fb",
}


def _brand_focus(row: dict[str, object]) -> str:
    focus = str(row.get("focus_category") or "").strip()
    return focus or str(row.get("niche") or "").strip()


def build_post_gate(platform: str, flow: str) -> JevPostGate | None:
    """The gate for this process's brand, or None when it should not run.

    Returns None when any of these holds:

    * the platform has no mode column;
    * ``OPENROUTER_API_KEY`` is unset (warned once);
    * the brand row cannot be read, or is not registered (warned);
    * the mode is off, which includes a DB that predates the mode columns.

    Never raises: an engager must start whether or not Jev is available.
    """
    column = MODE_COLUMNS.get(platform)
    if column is None:
        return None
    enabled = _enabled_mode(column)
    if enabled is None:
        return None
    brand_id, row, mode = enabled
    log.info("jev_gate_enabled", brand_id=brand_id, platform=platform, mode=mode)
    return JevPostGate(
        brand_id=brand_id, flow=flow, platform=platform, mode=mode, brand_focus=_brand_focus(row)
    )


def _enabled_mode(column: str) -> tuple[str, dict[str, object], GateMode] | None:
    """(brand_id, brand row, mode) when the gate in ``column`` should run.

    None when ``OPENROUTER_API_KEY`` is unset (warned once), the brand row
    cannot be read or is not registered (warned), or the mode is off --
    which includes a DB that predates the column. Never raises.
    """
    if not api_key_configured():
        warn_missing_key_once()
        return None
    brand_id = current_brand_id()
    try:
        row = brands_db.get(brand_id)
    except Exception as exc:
        log.warning("jev_gate_brand_lookup_failed", error_type=type(exc).__name__)
        return None
    if row is None:
        # Warning, not info: an id mismatch silently disables the gate.
        log.warning(
            "jev_gate_brand_unregistered",
            brand_id=brand_id,
            brand_dir=os.environ.get("BRAND_DIR", ""),
            persona_brand=os.environ.get("PERSONA_BRAND", ""),
        )
        return None
    mode = parse_mode(row.get(column))
    return None if mode == MODE_OFF else (brand_id, row, mode)


def build_group_gate(*, dry_run: bool = False) -> JevGroupGate | None:
    """fb-group-scout's gate, or None when it should not run.

    None under ``--dry-run`` (a dry run consumes no state, so it records
    none) and in every case ``_enabled_mode`` declines. Never raises.
    """
    if dry_run:
        return None
    enabled = _enabled_mode(GROUP_MODE_COLUMN)
    if enabled is None:
        return None
    brand_id, row, mode = enabled
    log.info("jev_gate_enabled", brand_id=brand_id, platform="fb_group", mode=mode)
    return JevGroupGate(brand_id=brand_id, mode=mode, brand_focus=_brand_focus(row))
