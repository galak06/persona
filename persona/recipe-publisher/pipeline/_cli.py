# pyright: reportMissingImports=false
"""Shared CLI helpers for pipeline phases.

Importing this module bridges ``sys.path`` so both the recipe-publisher root
(``recipe_db``/``pipeline``) and the social-automation root (``lib.*``) are
importable regardless of the current working directory — mirrors
api/recipes_api.py's path handling. Keeps every phase CLI free of duplicated
bootstrap code.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent
_SOCIAL_AUTOMATION = _RECIPE_PUBLISHER.parent
for _root in (_RECIPE_PUBLISHER, _SOCIAL_AUTOMATION):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


def get_phase_logger(phase: str, level: str = "INFO"):  # type: ignore[no-untyped-def]
    """Configure structured JSON logging and return a bound phase logger."""
    from lib.observability.logger import configure_logging, get_logger

    configure_logging(level=level)
    return get_logger(phase)
