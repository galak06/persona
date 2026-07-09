from __future__ import annotations

import logging

from lib.config import AppSettings, settings
from lib.logger import enable_unbuffered


def init_script(logger_name: str) -> tuple[AppSettings, logging.Logger]:
    """
    Standard initialization for all CLI scripts.
    Enables unbuffered stdout, ensures settings are loaded, and returns a configured logger.
    """
    enable_unbuffered()
    log = logging.getLogger(logger_name)
    return settings, log
