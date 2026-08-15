"""Append-only JSONL tracker of recently used affiliate products.

One line per drafted post: `{"ts": <UTC ISO>, "idea_id": ..., "keys": [...]}`
under `$BRAND_DIR/state/product_usage.jsonl`. Selection stages read
`recently_used_keys()` to avoid re-featuring the same product within the
TTL window.

The brand data rule is additive-only, so unlike `lib.draft_history` this
tracker NEVER rewrites or prunes the file -- expiry is a read-side filter
only. Default path resolution mirrors
`lib.fb_composer_reel._rotation_state_path`: no `BRAND_DIR` means no
default path, and every function degrades gracefully (tests always pass
an explicit `path`).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lib.observability import get_logger

logger = get_logger(__name__)

_USAGE_FILE_RELATIVE = Path("state") / "product_usage.jsonl"
_DEFAULT_WINDOW_DAYS = 30

# Which path placed a product. Recorded per event so a reader can scope
# the exclusion to its own history (see `recently_used_keys`).
SOURCE_DRAFT = "draft"  # the drafting pipeline, writing a new post
SOURCE_BACKFILL = "backfill"  # the sweep repairing already-published posts
SOURCE_LEGACY = "legacy"  # written before `source` existed


def _default_usage_path() -> Path | None:
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return None
    return Path(brand_dir) / _USAGE_FILE_RELATIVE


def record_usage(
    idea_id: str, keys: list[str], *, path: Path | None = None, source: str = SOURCE_DRAFT
) -> None:
    """Append one usage event. Creates the parent directory if needed.

    `source` records WHICH path placed these products, so readers can scope
    the exclusion (see `recently_used_keys`). Defaults to `"draft"` because
    the drafting path is the caller that must not re-feature its own recent
    picks; the published-post sweep passes `"backfill"`.
    """
    target = path or _default_usage_path()
    if target is None:
        logger.warning("product_usage_brand_dir_unset_record_skipped", idea_id=idea_id)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(UTC).isoformat(),
        "idea_id": idea_id,
        "keys": keys,
        "source": source,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def recently_used_keys(
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    path: Path | None = None,
    sources: set[str] | None = None,
) -> set[str]:
    """Product keys recorded within the last `window_days` days.

    `sources` restricts the count to events recorded by those paths; `None`
    (the default) counts every event, preserving the original behavior.

    **Why scoping matters.** Exclusion here is a hard filter -- an excluded
    product cannot be picked at all -- so a bulk write can empty the useful
    pool for a month. That happened: a one-off backfill sweep stamped 63
    events in a single day, putting 59 products out of reach, and the very
    next drafted post ("Fi Collar vs Tractive") could not feature `fi-collar`
    or `apple-airtag` because that sweep had placed them on unrelated older
    posts. Those two paths cover different content -- the sweep repairs posts
    already live, drafting writes new ones -- so the drafting path scopes
    itself to `{SOURCE_DRAFT}` and stops inheriting the sweep's history.

    Events written before `source` existed carry `SOURCE_LEGACY`. They are
    therefore invisible to a scoped read, which is the intended outcome:
    unattributable bulk history should not gate new work.

    Missing file (or unset `BRAND_DIR`) -> empty set. Corrupt lines are
    skipped with a debug log rather than failing the read.
    """
    target = path or _default_usage_path()
    if target is None or not target.exists():
        return set()
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    used: set[str] = set()
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                logger.debug("product_usage_corrupt_line_skipped", path=str(target))
                continue
            if not isinstance(row, dict):
                logger.debug("product_usage_non_object_line_skipped", path=str(target))
                continue
            if str(row.get("ts", "")) < cutoff:
                continue
            if sources is not None and str(row.get("source") or SOURCE_LEGACY) not in sources:
                continue
            keys = row.get("keys")
            if not isinstance(keys, list):
                continue
            used.update(key for key in keys if isinstance(key, str))
    return used
