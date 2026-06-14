"""Brand + Facebook-groups SQLite store (``${BRAND_DIR}/data/db/groups.db``).

Replaces ``data/trackers/groups_tracker.json`` as the single source of truth for
FB groups. The module-level helpers below mirror the JSON ergonomics so each
consumer swaps one read line and one write line:

    # before:  tracker = json.loads(TRACKER_FILE.read_text())
    # after:   tracker = groups_db.load_all()

    # before:  TRACKER_FILE.write_text(json.dumps(tracker, indent=2))
    # after:   groups_db.save_all(tracker)

Each helper opens → operates → closes a short-lived connection (WAL keeps locks
brief). For many operations in one process, use ``GroupsRepository`` directly.
"""

from __future__ import annotations

from typing import Any

from lib.groups_db.db import connect, migrate, resolve_groups_db_path
from lib.groups_db.models import GroupStatus, PostingMode
from lib.groups_db.repository import GroupsRepository

__all__ = [
    "GroupStatus",
    "GroupsRepository",
    "PostingMode",
    "append_note",
    "connect",
    "get_by_name",
    "get_by_url",
    "list_groups",
    "load_all",
    "migrate",
    "resolve_groups_db_path",
    "save_all",
    "set_posting_mode",
    "set_status",
]


def _repo() -> tuple[Any, GroupsRepository]:
    conn = connect()
    migrate(conn)
    return conn, GroupsRepository(conn)


def load_all() -> list[dict[str, Any]]:
    """All groups as tracker-shaped dicts."""
    conn, repo = _repo()
    try:
        return repo.load_all()
    finally:
        conn.close()


def save_all(groups: list[dict[str, Any]]) -> None:
    """Upsert every group from a tracker-shaped list."""
    conn, repo = _repo()
    try:
        repo.save_all(groups)
    finally:
        conn.close()


def list_groups(status: str | None = None) -> list[dict[str, Any]]:
    conn, repo = _repo()
    try:
        return repo.list_groups(status)
    finally:
        conn.close()


def get_by_url(group_url: str) -> dict[str, Any] | None:
    conn, repo = _repo()
    try:
        return repo.get_by_url(group_url)
    finally:
        conn.close()


def get_by_name(group_name: str) -> dict[str, Any] | None:
    conn, repo = _repo()
    try:
        return repo.get_by_name(group_name)
    finally:
        conn.close()


def set_status(group_url: str, status: str) -> bool:
    conn, repo = _repo()
    try:
        return repo.set_status(group_url, status)
    finally:
        conn.close()


def set_posting_mode(group_url: str, mode: str) -> bool:
    conn, repo = _repo()
    try:
        return repo.set_posting_mode(group_url, mode)
    finally:
        conn.close()


def append_note(group_url: str, note: dict[str, str]) -> bool:
    conn, repo = _repo()
    try:
        return repo.append_note(group_url, note)
    finally:
        conn.close()
