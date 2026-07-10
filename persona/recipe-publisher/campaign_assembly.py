"""Append a rotating teaser + CTA to FB captions.

The rotation tracker at `<BRAND_DIR>/state/campaign_rotation.json` stores
the next index per pool so consecutive prepare runs never pick the same
teaser or CTA back-to-back. Empty pools are a no-op (caller-side validator
in `tools/profiles_build.py` enforces non-empty when the campaign is opted
in via `link_in_first_comment`).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_DEFAULT_STATE: dict[str, int] = {"teaser_next_idx": 0, "cta_next_idx": 0}


def _read_state(path: Path) -> dict[str, int]:
    if not path.exists():
        return dict(_DEFAULT_STATE)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_STATE)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_STATE)
    return {
        "teaser_next_idx": int(raw.get("teaser_next_idx", 0) or 0),
        "cta_next_idx": int(raw.get("cta_next_idx", 0) or 0),
    }


def _write_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def append_teaser_and_cta(
    fb_caption: str,
    teasers: list[str],
    ctas: list[str],
    rotation_path: Path,
) -> str:
    """Return fb_caption with a rotating teaser + CTA appended.

    Format: '{caption}\\n\\n{teaser}\\n\\n{cta}'. If either pool is empty,
    returns the original caption unchanged so brands without a campaign
    block don't crash the prepare flow.
    """
    if not teasers or not ctas:
        return fb_caption

    state = _read_state(rotation_path)
    teaser_idx = state["teaser_next_idx"] % len(teasers)
    cta_idx = state["cta_next_idx"] % len(ctas)
    teaser = teasers[teaser_idx]
    cta = ctas[cta_idx]

    state["teaser_next_idx"] = (teaser_idx + 1) % len(teasers)
    state["cta_next_idx"] = (cta_idx + 1) % len(ctas)
    _write_state(rotation_path, state)

    return f"{fb_caption}\n\n{teaser}\n\n{cta}"
