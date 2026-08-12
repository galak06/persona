"""Shared environment resolution for the CrewAI pipeline scripts.

Extracted from `scripts/crewai_reels_pipeline.py` (which was at the 300-line
ceiling); `scripts/crewai_content_pipeline.py` keeps its own private copy of
the same convention for now.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent


def infer_brand_dir() -> Path:
    """`BRAND_DIR` env var, else the single folder under `brands/`; raises
    SystemExit when neither identifies exactly one brand."""
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "BRAND_DIR not set and brands/ doesn't have exactly one brand folder -- pass --brand-dir"
    )


def check_required_env(names: tuple[str, ...]) -> list[str]:
    """The subset of `names` that is unset or blank in the environment."""
    return [name for name in names if not os.environ.get(name, "").strip()]
