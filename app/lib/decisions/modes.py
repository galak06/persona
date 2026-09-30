"""The gate-mode vocabulary shared by the engine, the API and the DB CHECK.

``off``      -- the gate is not consulted at all.
``shadow``   -- the gate is consulted and its decision logged; the flow is
                never altered by it.
``enforce``  -- the gate's ``would_skip`` actually skips the drafter.

Kept dependency-free so ``api/brand_schemas.py`` can import it without
pulling in ``httpx``.
"""

from __future__ import annotations

from typing import Final, Literal

GateMode = Literal["off", "shadow", "enforce"]

MODE_OFF: Final = "off"
MODE_SHADOW: Final = "shadow"
MODE_ENFORCE: Final = "enforce"

GATE_MODES: Final[tuple[GateMode, ...]] = ("off", "shadow", "enforce")

# Must match the brands-column DEFAULT in db/schema.sql.
DEFAULT_MODE: Final[GateMode] = "shadow"


def parse_mode(value: object) -> GateMode:
    """Normalise a stored mode; anything unrecognised fails safe to ``off``.

    ``off`` rather than the column default: a value the DB CHECK should have
    rejected means something is wrong, and the safe reaction is to not call
    a paid API at all.
    """
    text = str(value or "").strip().lower()
    for mode in GATE_MODES:
        if text == mode:
            return mode
    return "off"
