"""Hold Telegram alerts that could not be delivered, and resend them later.

The failure this exists for is circular: the engagers fail *because* the
container lost DNS, and the alert about that failure is itself a request to
`api.telegram.org` -- which fails for the identical reason. So the one run
you most needed to hear about is the one that tells you nothing. The
2026-08-26 `fb-engager` run is exactly that: it died on
`ERR_NAME_NOT_RESOLVED`, then logged
`Failed to resolve 'api.telegram.org'` and went silent.

An undelivered alert is parked here and flushed on the next send that
succeeds, so the notification survives the outage that caused it.

Every function swallows its own errors. The notifier is on critical paths
(approval gates included) and an outbox problem must never be able to break
the thing it is trying to report on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Bounded so a long outage cannot grow the file without limit. Oldest entries
# are dropped first: during a multi-day outage the recent alerts are the ones
# still worth reading.
MAX_PENDING = 50

_FILENAME = "notifier_outbox.jsonl"


def outbox_path() -> Path | None:
    """The brand's outbox file, or None when there is no brand context.

    None (rather than a raise) keeps non-brand-scoped callers working, matching
    how `lib.notifier._get_config_file` already degrades.
    """
    try:
        from lib.config import settings

        paths = settings.paths
    except Exception:
        return None
    if paths is None:
        return None
    return paths.state_dir / _FILENAME


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # one corrupt line must not lose the rest
            if isinstance(entry, dict) and entry.get("message"):
                entries.append(entry)
    except OSError:
        return []
    return entries


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def enqueue(message: str, *, silent: bool = False) -> bool:
    """Park one undeliverable alert. True if it was stored."""
    path = outbox_path()
    if path is None or not message:
        return False
    entries = _read(path)
    entries.append(
        {
            "queued_at": datetime.now(UTC).isoformat(),
            "message": message,
            "silent": silent,
        }
    )
    _write(path, entries[-MAX_PENDING:])
    return True


def pending_count() -> int:
    path = outbox_path()
    return len(_read(path)) if path is not None else 0


def drain(send_fn: Callable[[str, bool], bool], *, limit: int = MAX_PENDING) -> int:
    """Resend parked alerts via `send_fn(message, silent)`; return how many went.

    Stops at the first failure and keeps that entry (plus everything after it)
    so ordering survives and a still-broken network doesn't burn the queue.
    """
    path = outbox_path()
    if path is None:
        return 0
    entries = _read(path)
    if not entries:
        return 0

    sent = 0
    for index, entry in enumerate(entries[:limit]):
        try:
            ok = send_fn(str(entry.get("message", "")), bool(entry.get("silent", False)))
        except Exception:
            ok = False
        if not ok:
            _write(path, entries[index:])
            return sent
        sent += 1

    _write(path, entries[sent:])
    return sent
