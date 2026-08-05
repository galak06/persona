"""Per-campaign worker.lock — fcntl.flock + 1-hour stale fallback.

Preserves the lock semantics of the original ``scripts/campaign_worker.py``:
if the lock file exists and is younger than 1 hour, the campaign is
considered locked; older means stale and gets cleared. Distinct from
``lib.runtime.singleton.SingletonLock`` which is a global per-runner lock.
"""

from __future__ import annotations

import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from lib.observability.logger import get_logger

log = get_logger(__name__)

_STALE_LOCK_SECONDS = 3600  # 1 hour — matches the original worker


class LockHeldError(RuntimeError):
    """Another process holds this campaign's ``worker.lock``."""


class CampaignLock:
    """Context manager wrapping the per-campaign worker.lock.

    Closing the fd drops the flock; we also unlink the path so the
    next inspector doesn't see a stale file (matches the original
    worker's ``lock_file.unlink()`` in its finally block).
    """

    def __init__(self, handle: IO[str], path: Path, campaign_name: str) -> None:
        self.handle = handle
        self.path = path
        self.campaign_name = campaign_name

    def __enter__(self) -> CampaignLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            self.handle.close()
        finally:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.error(
                    "campaign_lock_unlink_failed",
                    campaign=self.campaign_name,
                    error=str(exc),
                )


def acquire(lock_file: Path, campaign_name: str) -> CampaignLock:
    """Acquire ``worker.lock`` with the original 1-hour stale fallback.

    Sequence:
        1. If the lock file exists and is younger than 1 hour, raise
           :class:`LockHeldError`.
        2. If older than 1 hour, log + unlink (stale).
        3. Open the file and ``flock(LOCK_EX | LOCK_NB)``. If contended,
           raise :class:`LockHeldError`.
    """
    now = datetime.now(timezone.utc)
    if lock_file.exists():
        try:
            mtime = datetime.fromtimestamp(lock_file.stat().st_mtime, tz=timezone.utc)
        except OSError as exc:
            raise LockHeldError(
                f"cannot stat lock file for {campaign_name}: {exc}"
            ) from exc
        age = (now - mtime).total_seconds()
        if age < _STALE_LOCK_SECONDS:
            raise LockHeldError(
                f"campaign {campaign_name!r} is locked "
                f"(held since {mtime.isoformat()})"
            )
        log.warning(
            "campaign_lock_stale_removed", campaign=campaign_name, age_seconds=age
        )
        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle: IO[str] = open(lock_file, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise LockHeldError(
            f"campaign {campaign_name!r} is locked (flock contended)"
        ) from exc
    return CampaignLock(handle, lock_file, campaign_name)
