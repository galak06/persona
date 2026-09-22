"""Portable font resolution for the overlay renderer.

`generators.text_overlay` picks its fonts from ``OVERLAY_*`` environment
variables at import time, defaulting to macOS system paths. Those paths do
not exist on Linux or Windows, so a fresh clone would crash on the first
slide. This module resolves a real font for the host platform and exports
the variables *before* `text_overlay` is imported.

An operator who has already set ``OVERLAY_HEADLINE_FONT`` keeps their
choice — we only fill in what is missing.
"""

from __future__ import annotations

import os
from pathlib import Path

# Bold display faces, best first. Arial Black is the historical default and
# stays first so existing macOS output is byte-identical.
_HEADLINE_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
)

_SUBCOPY_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)


class NoUsableFontError(RuntimeError):
    """Raised when no font could be found for text rendering."""


def _first_present(candidates: tuple[str, ...]) -> str | None:
    return next((c for c in candidates if Path(c).is_file()), None)


def resolve_fonts() -> tuple[str, str]:
    """Export OVERLAY_* font env vars if unset. Returns (headline, subcopy).

    Must be called before importing `generators.text_overlay`, which reads
    these variables at module import time.
    """
    headline = os.environ.get("OVERLAY_HEADLINE_FONT") or _first_present(_HEADLINE_CANDIDATES)
    subcopy = os.environ.get("OVERLAY_SUBCOPY_FONT") or _first_present(_SUBCOPY_CANDIDATES)

    if not headline or not subcopy:
        raise NoUsableFontError(
            "No usable TrueType font found. Install a font package "
            "(Debian/Ubuntu: `apt-get install fonts-dejavu-core`, "
            "Fedora: `dnf install dejavu-sans-fonts`), or point "
            "OVERLAY_HEADLINE_FONT and OVERLAY_SUBCOPY_FONT at .ttf files."
        )

    os.environ["OVERLAY_HEADLINE_FONT"] = headline
    os.environ["OVERLAY_SUBCOPY_FONT"] = subcopy
    # Index 1 selects Helvetica Bold inside the macOS .ttc collection; a
    # standalone .ttf has only face 0.
    if "OVERLAY_SUBCOPY_FONT_INDEX" not in os.environ:
        os.environ["OVERLAY_SUBCOPY_FONT_INDEX"] = "1" if subcopy.endswith(".ttc") else "0"
    return headline, subcopy
