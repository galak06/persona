from __future__ import annotations

import logging

from lib.config import AppSettings, settings
from lib.logger import enable_unbuffered
from lib.observability.logger import configure_logging


def init_script(logger_name: str) -> tuple[AppSettings, logging.Logger]:
    """
    Standard initialization for all CLI scripts.
    Enables unbuffered stdout, ensures settings are loaded, and returns a configured logger.

    `configure_logging()` attaches stdout logging.basicConfig() -- without
    it, `logging.getLogger(logger_name)` has no handler anywhere in its
    chain, so every `.info()`/`.warning()` call this returned logger makes
    is silently dropped (confirmed: every log.info() call in
    lib/engagement/pipeline.py, across every flow using this helper, has
    never actually printed anything). Idempotent -- harmless if a script
    also calls it directly.
    """
    enable_unbuffered()
    configure_logging()
    log = logging.getLogger(logger_name)
    return settings, log
