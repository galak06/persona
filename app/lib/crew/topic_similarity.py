"""Fuzzy topic-duplicate check for the CrewAI scout (`lib.crew.scout`).

Independent copy of the same exact-then-fuzzy pattern already proven in
`recipe-publisher/workers/worker_content_ideator.py::_is_duplicate` -- kept
as a separate small module rather than imported/shared, per this codebase's
established "independently-evolving pipelines" convention (each pipeline
keeps its own small utility copies rather than cross-importing; already the
case between `writer`/`editor`/`trends`/`idea`). Stdlib `difflib` only, no
new pip dependency.
"""

from __future__ import annotations

import difflib


def is_similar_topic(topic: str, existing: set[str], *, threshold: float = 0.85) -> bool:
    """True if `topic` exactly matches (case-insensitive) or is a close fuzzy
    match to any string in `existing`.

    `existing` is expected already-lowercased (matching `ideas_db.existing_topics`'s
    contract), but `topic` is lowercased here defensively before comparing.
    """
    lower = topic.strip().lower()
    if lower in existing:
        return True
    return any(difflib.SequenceMatcher(None, lower, t).ratio() > threshold for t in existing)
