"""Persistent, additive store of keywords discovered by the Trends stage.

The scout used to run against a frozen vocabulary: `content_analysis.keywords`
in the brand's `config.json`, hand-maintained and never updated by a run. For
dogfoodandfun that is 73 terms, 49 of them food, so every run searched the
same space and the Idea agent converged on the same subjects -- three
Canicross posts, two pumpkin-vs-sweet-potato posts, two Fi-vs-Tractive posts.
Better dedup only suppresses the repeats; it cannot widen the space they are
drawn from. This module widens it.

**The research already happens.** `lib.crew.trends` merges GSC ranking data,
live Serper search, and the Instagram trends feed into scored `TrendSignal`s
whose `keyword` field is exactly a market-researched keyword -- and then
throws them away at the end of the run. So this needs no extra crew, agent,
or LLM round trip: it harvests what the Trends stage already found and keeps
it, so run N+1 searches a vocabulary that run N discovered. That is the whole
feedback loop.

Two deliberate constraints:

* **Additive only.** An entry is created or updated, never removed or
  rewritten in place -- `first_seen` is preserved on every later sighting.
  A term that stops trending stays on file rather than silently vanishing
  from the brand's vocabulary.
* **Written HERE, not into `config.json`.** `content_analysis.keywords` is
  read by the FB/IG engagement scanners too (`lib.engagement.policy`,
  `lib.comment_generator`, `scripts/ig_like.py`), where it drives
  `score_relevance`. Appending scout discoveries to it would silently
  re-tune which Facebook and Instagram posts get commented on -- a coupling
  that has already caused one live incident when onboarding narrowed that
  same block. A separate file keeps the curated config the operator's, and
  makes the discovered set independently inspectable and deletable.

`active_seeds` bounds what reaches a prompt: the store grows forever by
design, but a run should not paste thousands of terms into an agent's
context, so the highest-scoring slice is what actually gets used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.crew.trends.models import TrendSignal
from lib.gsc_scout_scoring import KeywordSeed
from lib.io.jsonio import locked_json
from lib.observability import get_logger

logger = get_logger(__name__)

_STORE_VERSION = 1
_RELATIVE_PATH = Path("state") / "discovered_keywords.json"
# What a single run may contribute to an agent's context. The store itself is
# unbounded (additive by design); this only caps the slice that is rendered.
DEFAULT_ACTIVE_LIMIT = 60

# The scout tags its own seeds `content_analysis:<category>`; discovered ones
# carry this instead so a row's provenance stays readable downstream.
DISCOVERED_SOURCE = "trends_discovery"


def store_path(brand_dir: Path) -> Path:
    return brand_dir / _RELATIVE_PATH


def _normalise(keyword: str) -> str:
    """Dict key for a term: trimmed and lowercased.

    Case and padding are the only normalisation -- no stemming. Two genuinely
    different phrasings of one idea are worth keeping separately here, because
    unlike a topic (where a duplicate wastes an editorial slot) a near-synonym
    keyword is a real, cheap extra search angle.
    """
    return keyword.strip().lower()


def load(brand_dir: Path) -> dict[str, dict[str, Any]]:
    """Every discovered keyword on file, keyed by normalised term."""
    path = store_path(brand_dir)
    if not path.exists():
        return {}
    try:
        with locked_json(path, {"version": _STORE_VERSION, "keywords": {}}) as data:
            raw = data.get("keywords") if isinstance(data, dict) else None
            return dict(raw) if isinstance(raw, dict) else {}
    except OSError as exc:
        # Same "never crash the run" contract the rest of the scout follows: a
        # missing or unreadable store degrades to the curated vocabulary.
        logger.warning("keyword_store_load_failed", path=str(path), error=str(exc))
        return {}


def record_signals(
    brand_dir: Path,
    signals: list[TrendSignal],
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Upsert `signals` into the store. Returns `(new, updated)` counts.

    Never raises -- a failed write costs the next run some vocabulary, and
    must not fail a scout run that otherwise produced real ideas.
    """
    if not signals:
        return (0, 0)
    stamp = (now or datetime.now(UTC)).isoformat()
    path = store_path(brand_dir)
    new = updated = 0
    try:
        with locked_json(path, {"version": _STORE_VERSION, "keywords": {}}) as data:
            if not isinstance(data, dict):
                return (0, 0)
            data.setdefault("version", _STORE_VERSION)
            keywords = data.setdefault("keywords", {})
            if not isinstance(keywords, dict):
                return (0, 0)
            for signal in signals:
                key = _normalise(signal.keyword)
                if not key:
                    continue
                existing = keywords.get(key)
                if existing is None:
                    keywords[key] = {
                        "keyword": signal.keyword.strip(),
                        "category": signal.category,
                        "first_seen": stamp,
                        "last_seen": stamp,
                        "times_seen": 1,
                        "best_score": float(signal.score),
                        "reason": signal.reason,
                    }
                    new += 1
                    continue
                # Additive update: first_seen is never rewritten, and the score
                # only ever moves up, so one weak sighting cannot demote a term
                # that ranked strongly before.
                existing["last_seen"] = stamp
                existing["times_seen"] = int(existing.get("times_seen") or 0) + 1
                existing["best_score"] = max(
                    float(existing.get("best_score") or 0.0), float(signal.score)
                )
                updated += 1
    except OSError as exc:
        logger.warning("keyword_store_write_failed", path=str(path), error=str(exc))
        return (0, 0)
    return (new, updated)


def active_seeds(
    brand_dir: Path,
    *,
    limit: int | None = None,
    exclude: set[str] | None = None,
) -> list[KeywordSeed]:
    """The highest-scoring discovered terms, as `KeywordSeed`s.

    `exclude` (normalised terms already in the curated config) keeps a term
    the operator already curated from occupying one of the limited slots.
    Ordered by best_score, then times_seen, then the term itself so the
    result is stable for a given store rather than dependent on dict order.

    `limit` defaults to `DEFAULT_ACTIVE_LIMIT`, resolved at CALL time rather
    than bound as a default argument: a module-level constant used as a
    default is captured when the function is defined, so overriding it later
    (a test, or a future per-brand setting) would silently have no effect.
    """
    resolved_limit = DEFAULT_ACTIVE_LIMIT if limit is None else limit
    skip = exclude or set()
    entries = [
        (key, entry)
        for key, entry in load(brand_dir).items()
        if key not in skip and isinstance(entry, dict)
    ]
    entries.sort(
        key=lambda kv: (
            -float(kv[1].get("best_score") or 0.0),
            -int(kv[1].get("times_seen") or 0),
            kv[0],
        )
    )
    return [
        KeywordSeed(
            keyword=str(entry.get("keyword") or key),
            category=str(entry.get("category") or "General"),
            source=DISCOVERED_SOURCE,
        )
        for key, entry in entries[: max(0, resolved_limit)]
    ]
