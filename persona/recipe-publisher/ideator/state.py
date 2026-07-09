"""Persistent state for the recipe ideator.

Three sources of truth read on every run:
    seeds.json              — current queue (drains every 2 days)
    published_recipes.json  — what's already shipped
    ideator_history.json    — proposals from prior runs (approved + skipped)

Mutations are atomic: write to <path>.tmp, fsync, rename. Never partial.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SEEDS_FILE: Final[Path] = PROJECT_ROOT / "seeds" / "seeds.json"
PUBLISHED_FILE: Final[Path] = PROJECT_ROOT / "state" / "published_recipes.json"
HISTORY_FILE: Final[Path] = PROJECT_ROOT / "state" / "ideator_history.json"


@dataclass(frozen=True)
class ExistingContext:
    """Everything the ideator needs to deduplicate."""

    seed_titles: tuple[str, ...]
    published_titles: tuple[str, ...]
    history_titles: tuple[str, ...]

    @property
    def all_titles(self) -> tuple[str, ...]:
        return self.seed_titles + self.published_titles + self.history_titles


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _normalize(title: str) -> str:
    """Lowercase + collapse whitespace + drop punctuation, for fuzzy compare."""
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


def load_existing_context() -> ExistingContext:
    seeds_payload = _load_json(SEEDS_FILE, {"seeds": []})
    seeds = seeds_payload.get("seeds", []) if isinstance(seeds_payload, dict) else []
    seed_titles = tuple(s.get("title", "") for s in seeds if isinstance(s, dict))

    published_payload = _load_json(PUBLISHED_FILE, [])
    published_titles = tuple(
        p.get("title", "") for p in published_payload if isinstance(p, dict)
    )

    history_payload = _load_json(HISTORY_FILE, {"runs": []})
    history_titles: list[str] = []
    for run in history_payload.get("runs", []):
        for cand in run.get("candidates", []):
            t = cand.get("title")
            if t:
                history_titles.append(t)

    return ExistingContext(
        seed_titles=seed_titles,
        published_titles=published_titles,
        history_titles=tuple(history_titles),
    )


def is_duplicate_title(title: str, ctx: ExistingContext) -> bool:
    """Fuzzy match against every known title."""
    needle = _normalize(title)
    if not needle:
        return False
    for existing in ctx.all_titles:
        haystack = _normalize(existing)
        if needle == haystack or needle in haystack or haystack in needle:
            return True
    return False


def append_seed(seed: dict[str, Any]) -> None:
    """Idempotently add a seed to seeds.json. Skips if id already present."""
    payload = _load_json(SEEDS_FILE, {"_schema_version": 1, "seeds": []})
    if not isinstance(payload, dict):
        payload = {"_schema_version": 1, "seeds": []}
    payload.setdefault("seeds", [])
    if any(s.get("id") == seed["id"] for s in payload["seeds"] if isinstance(s, dict)):
        return  # already there — no-op
    payload["seeds"].append(seed)
    _atomic_write(SEEDS_FILE, payload)


def record_run(
    *,
    candidates: list[dict[str, Any]],
    approved_seed_ids: list[str],
    notes: str = "",
) -> None:
    """Append a run record to ideator_history.json."""
    payload = _load_json(HISTORY_FILE, {"_schema_version": 1, "runs": []})
    if not isinstance(payload, dict):
        payload = {"_schema_version": 1, "runs": []}
    payload.setdefault("runs", []).append(
        {
            "ran_at": datetime.now(UTC).isoformat(),
            "candidates": candidates,
            "approved_seed_ids": approved_seed_ids,
            "notes": notes,
        }
    )
    _atomic_write(HISTORY_FILE, payload)
