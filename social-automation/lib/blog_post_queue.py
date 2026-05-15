# pyright: reportMissingImports=false
"""Blog-post pair queue: thin domain wrapper around ``lib/queue_state.py``.

The web UI and Telegram approver both decide on FB+IG caption pairs for
freshly-published WordPress posts. This module owns the producer side:
``stage_publish`` calls :func:`enqueue_blog_post_pair` *before* sending the
approval message to Telegram, so the web UI sees the item the instant the
approval gate opens.

Schema is the same as :class:`api.schemas.BlogPostItem` — keep both in sync
when extending. ``commit_decision`` (in ``api/state.py``) is the only writer
that flips ``status`` to a terminal value; this module only enqueues + reads
+ drops.

Why this isn't in ``api/`` — producers live under ``lib/`` so the FastAPI
sidecar's import graph stays read-only on application code.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from lib.config import settings
import os
import sys
from pathlib import Path
from typing import Any

# Mirror the sys.path tweak from ``lib/queue_state.py`` so ``api.*`` resolves
# when this module is loaded from scripts that only prepend ``lib/``.
_API_PARENT = Path(__file__).resolve().parent.parent
if str(_API_PARENT) not in sys.path:
    sys.path.insert(0, str(_API_PARENT))

from lib.queue_state import (  # noqa: E402  (sys.path tweak must precede)
    read_decision,
    utc_now_iso,
    write_pending,
)

QUEUE_PATH: Path = (
    settings.paths.state_dir / "blog_post_queue.json"
)


def _derive_pair_id(*, post_id: int, post_url: str, fb_caption: str, ig_caption: str) -> str:
    """Stable 12-char id keyed on the WP post + caption pair.

    Re-running the publisher with the same captions returns the same id so a
    retry never enqueues a duplicate pending row. ``api.state.derive_item_id``
    would also work, but it derives from ``platform``/``post_id`` and blog-post
    items carry no ``platform`` field — so we precompute here and let
    ``write_pending`` honour the explicit ``id``.
    """
    seed = f"blog_post:{post_id}:{post_url}:{fb_caption}:{ig_caption}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def enqueue_blog_post_pair(
    *,
    post_id: int,
    post_title: str,
    post_url: str,
    fb_caption: str,
    ig_caption: str,
    image_url: str | None = None,
) -> str:
    """Append a pending blog-post pair and return its ``item_id``.

    Idempotent: a second call with identical inputs returns the same id and
    does NOT create a duplicate row. Delegation to ``write_pending`` keeps the
    flock + atomic-write contract owned by a single module.
    """
    item_id = _derive_pair_id(
        post_id=post_id,
        post_url=post_url,
        fb_caption=fb_caption,
        ig_caption=ig_caption,
    )
    item: dict[str, Any] = {
        "type": "blog_post",
        "id": item_id,
        "post_id": post_id,
        "post_title": post_title,
        "post_url": post_url,
        "fb_caption": fb_caption,
        "ig_caption": ig_caption,
        "image_url": image_url,
        "status": "pending",
        "decided_by": None,
        "decided_at": None,
        "channel": None,
        "created_at": utc_now_iso(),
    }
    return write_pending(QUEUE_PATH, item)


def get_decision(item_id: str) -> dict[str, Any] | None:
    """Return the current row for ``item_id`` (or ``None`` if absent).

    Thin alias over ``lib.queue_state.read_decision`` so callers can stick to
    the blog-post namespace without learning the underlying primitives.
    """
    return read_decision(QUEUE_PATH, item_id)


def mark_published(item_id: str) -> None:
    """Drop the item from the queue.

    We physically remove rather than stamping ``status='published'`` so the
    web UI's ``GET /pending`` stays cheap — no need to filter terminal rows
    on every request. No-op if the item is missing.
    """
    if not QUEUE_PATH.exists():
        return

    with QUEUE_PATH.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            raw = fh.read()
            try:
                data_loaded = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                return
            if not isinstance(data_loaded, list):
                return
            remaining: list[dict[str, Any]] = [
                item
                for item in data_loaded
                if isinstance(item, dict) and item.get("id") != item_id
            ]
            if len(remaining) == len(data_loaded):
                return

            tmp_path = QUEUE_PATH.with_suffix(QUEUE_PATH.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(remaining, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, QUEUE_PATH)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


__all__ = [
    "QUEUE_PATH",
    "enqueue_blog_post_pair",
    "get_decision",
    "mark_published",
]
