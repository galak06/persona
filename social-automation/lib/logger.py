"""
Timestamped, unbuffered logger for DogFoodAndFun scripts.
Replaces print() — every line gets a timestamp and flushes immediately.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone


def _ts() -> str:
    """Short UTC timestamp for log lines."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg: str, level: str = "INFO") -> None:
    """Print a timestamped, flushed log line."""
    print(f"[{_ts()}] {level}: {msg}", flush=True)


def log_step(step: str, detail: str = "") -> None:
    """Log a major step (e.g. 'Scanning group 3/7')."""
    line = f"[{_ts()}] >> {step}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


def log_progress(current: int, total: int, label: str, extra: str = "") -> None:
    """Log progress like '[12:30:01] [3/7] Scanning: Group Name'."""
    line = f"[{_ts()}] [{current}/{total}] {label}"
    if extra:
        line += f" — {extra}"
    print(line, flush=True)


def log_warn(msg: str) -> None:
    log(msg, level="WARN")


def log_error(msg: str) -> None:
    log(msg, level="ERROR")


def log_skip(msg: str) -> None:
    log(msg, level="SKIP")


def enable_unbuffered() -> None:
    """Force stdout/stderr to line-buffered mode (no buffering delays)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)


class StepTimer:
    """Context manager that logs how long a step took."""

    def __init__(self, label: str):
        self.label = label
        self.start = 0.0

    def __enter__(self) -> "StepTimer":
        self.start = time.monotonic()
        log_step(self.label, "started")
        return self

    def __exit__(self, *exc) -> None:
        elapsed = time.monotonic() - self.start
        if elapsed < 60:
            dur = f"{elapsed:.1f}s"
        else:
            dur = f"{elapsed / 60:.1f}m"
        status = "done" if not exc[0] else f"FAILED ({exc[1]})"
        log_step(self.label, f"{status} ({dur})")
