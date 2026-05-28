"""Centralised helper for appending notes to FB group records.

Single source of truth for the {at, text} note shape. All writers in
scripts/ that touch a group's `notes` list MUST call this helper instead
of building the dict inline — that prevents the kind of shape drift that
broke /api/v1/facebook/groups previously (a writer initialised `notes`
as `""` and string-concatenated entries, violating the FacebookGroup
schema's `list[dict[str, str]]` contract).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _now_iso_utc() -> str:
    """Return current UTC time as an ISO-8601 string with 'Z' suffix.

    Matches the existing canonical format used across the codebase
    (e.g. fb_groups_posting_scan.py:119, fb_group_note.py:113).
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def append_group_note(group: dict[str, Any], text: str) -> None:
    """Append a `{at, text}` note to `group["notes"]`.

    Initialises the list if it does not exist. Coerces a legacy string
    `notes` value to a single-element list before appending, so callers
    cannot accidentally restore the broken string shape.
    """
    existing = group.get("notes")
    if existing is None:
        group["notes"] = []
    elif isinstance(existing, str):
        # Legacy data shape — defensive coercion; migration script
        # normally handles this. Empty string → empty list.
        group["notes"] = (
            [{"at": _now_iso_utc(), "text": existing}] if existing.strip() else []
        )
    elif not isinstance(existing, list):
        raise TypeError(
            f"group['notes'] must be list, got {type(existing).__name__}"
        )
    group["notes"].append({"at": _now_iso_utc(), "text": text})
