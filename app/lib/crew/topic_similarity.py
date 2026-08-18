"""Fuzzy topic-duplicate check for the CrewAI scout (`lib.crew.scout`).

Independent copy of the same exact-then-fuzzy pattern already proven in
`recipe-publisher/workers/worker_content_ideator.py::_is_duplicate` -- kept
as a separate small module rather than imported/shared, per this codebase's
established "independently-evolving pipelines" convention (each pipeline
keeps its own small utility copies rather than cross-importing; already the
case between `writer`/`editor`/`trends`/`idea`). Stdlib `difflib` only, no
new pip dependency.

The whole-string `difflib` ratio alone was not enough. It compares the
*headline*, and the Idea agent writes a fresh marketing headline every run,
so two rows covering an identical subject scored nowhere near the 0.85 gate
and both got inserted. Measured on the 36 live rows:

    0.63  "Pumpkin vs Sweet Potato for Dog Digestion: Which One Fixed..."
          "Sweet Potato vs. Pumpkin for Dogs: Which One Helped..."
    0.43  "Canicross for Beginners: How Nalla and I Started Running..."
          "Canicross for Beginners: The 5 Gear Mistakes We Made..."
    0.37  "Fi Collar vs Tractive: 3 Months With Both on Nalla..."
          "Tractive vs Fi 2026: Which GPS Tracker We'd Trust..."

Nothing was caught, which is why the table holds three Canicross posts and
two pumpkin-vs-sweet-potato posts. So a second check compares SUBJECT rather
than prose: the tokens before the first colon (the head, where the topic
lives -- everything after it is the hook), stopworded and singularised, as a
Jaccard overlap.

Both guards on that second check are load-bearing, and were calibrated
against all 630 pairs of the 36 live rows:

* **Equal token sets always match**, whatever their size. "Canicross for
  Beginners" reduces to `{canicross, beginner}` -- only two tokens, so the
  size floor below would otherwise skip the most blatant duplicate here.
* **Otherwise both sides need >= `_MIN_SUBJECT_TOKENS`.** A stub topic like
  the literal row "Dog food" reduces to `{dog, food}`, which overlaps every
  food headline this brand will ever write (it scored 0.67 against "Raw Dog
  Food: What the 2025 Studies Actually Say"). Without the floor a short row
  becomes a black hole that suppresses whole subject areas.

At `_SUBJECT_THRESHOLD = 0.60` that flags 4 of 630 pairs on live data, all
four genuine duplicates, no false positives. Erring toward under-matching is
deliberate: a missed duplicate costs one redundant row a human can skip, a
false match silently discards a genuinely new idea with no record of what
was lost.
"""

from __future__ import annotations

import difflib
import re

# Generic English function words only. Deliberately NOT brand terms ("dog",
# "nalla"): this module is shared across brands, and a brand-tuned list here
# would silently change dedup behaviour for every other brand on the engine.
# The size floor below is what defuses the generic-token problem instead.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "for",
        "and",
        "with",
        "we",
        "i",
        "our",
        "your",
        "you",
        "vs",
        "one",
        "which",
        "what",
        "how",
        "before",
        "both",
        "on",
        "to",
        "of",
        "in",
        "is",
        "are",
        "that",
        "this",
        "it",
        "its",
        "my",
        "me",
        "us",
        "but",
        "or",
        "from",
        "after",
        "when",
        "why",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "no",
        "can",
    }
)

_SUBJECT_THRESHOLD = 0.60
_MIN_SUBJECT_TOKENS = 3
_WORD_RE = re.compile(r"[a-z0-9]+")


def _singularise(word: str) -> str:
    """Crude plural folding, so "Dogs"/"Dog" don't split an otherwise clean
    match -- on the live pumpkin pair that difference alone drops the score
    from 0.80 to 0.50.

    Deliberately not a real stemmer (no new dependency for a comparison this
    coarse). It only needs to be CONSISTENT, not linguistically right:
    "series" -> "sery" is fine because both sides fold identically. The
    `-es`/`-ies` branches exist because bare `-s` stripping turns "potatoes"
    into "potatoe", which then fails to match "potato" -- exactly the kind of
    near-miss this function is here to prevent.
    """
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("oes", "ches", "shes", "xes", "ses", "zes")):
        return word[:-2]
    return word[:-1]


def subject_tokens(topic: str) -> frozenset[str]:
    """Normalised content words from `topic`'s head (before the first colon).

    The head carries the subject; the tail is the hook and is exactly the
    part the agent rewrites every run. Tokens are lowercased, stripped of
    stopwords and 1-2 character fragments, and crudely singularised so
    "Dogs" and "Dog" collapse -- without that, the pumpkin pair scores 0.50
    instead of 0.80 purely on a plural.
    """
    head = topic.split(":", 1)[0]
    out: set[str] = set()
    for word in _WORD_RE.findall(head.lower()):
        if len(word) <= 2 or word in _STOPWORDS:
            continue
        out.add(_singularise(word))
    return frozenset(out)


def _same_subject(a: frozenset[str], b: frozenset[str]) -> bool:
    """Jaccard overlap of two token sets, under the two guards above."""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) < _MIN_SUBJECT_TOKENS or len(b) < _MIN_SUBJECT_TOKENS:
        return False
    return len(a & b) / len(a | b) >= _SUBJECT_THRESHOLD


def is_similar_topic(topic: str, existing: set[str], *, threshold: float = 0.85) -> bool:
    """True if `topic` duplicates anything in `existing`.

    Three escalating checks: exact match, whole-string fuzzy match at
    `threshold` (both unchanged), then a subject-token overlap that catches
    the same-topic-different-headline case the first two miss entirely.

    `existing` is expected already-lowercased (matching `ideas_db.existing_topics`'s
    contract), but `topic` is lowercased here defensively before comparing.
    """
    lower = topic.strip().lower()
    if lower in existing:
        return True
    if any(difflib.SequenceMatcher(None, lower, t).ratio() > threshold for t in existing):
        return True
    mine = subject_tokens(lower)
    if not mine:
        return False
    return any(_same_subject(mine, subject_tokens(other)) for other in existing)
