#!/usr/bin/env python3
"""Trivial stand-in flow for `task_dispatcher.py` verification.

Logs one line and exits 0 -- a placeholder for a real scheduled flow (e.g.
`ig_scan.py`/`fb_scan.py`) so the Phase A dispatcher can be exercised
end-to-end (seed a `schedule_tasks` row, dispatch, confirm `worker_runs`)
without depending on browser automation or live platform credentials.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

_, logger = init_script(__name__)


def main() -> None:
    logger.info("noop_healthcheck ran at %s", datetime.now(UTC).isoformat())


if __name__ == "__main__":
    main()
