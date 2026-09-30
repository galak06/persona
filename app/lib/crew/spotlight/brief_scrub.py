"""Keep packaging nouns out of a spotlight's image brief -- including negated ones.

Rule 5 (``rules._image_brief_violations``) blocks any packaging noun in
``image_brief``, because an image model asked for a branded bag paints a fake
label onto a real product. The writer answers that instruction the way a
person would: "Nothing in frame carries text, labels or packaging". That
sentence is the brief OBEYING the rule, yet a bare noun match rejects it, and
on 2026-09-30 it spent all three retries of the first live spotlight
(``plan_failed``) and two of the three on the next.

Why a negated sentence is DROPPED rather than merely excused: image models do
not parse negation. "no labels" is a prompt that mentions labels, and a
diffusion model draws what a prompt mentions. So the same scrubbed text is
what the rule checks AND what ``compose`` hands the image model -- the words
never reach the picture either way.

Why whole sentences, and why a scope this generous: a misread here can only
ever REMOVE a sentence from the image prompt, never let a packaging noun
through. "A dog without a collar beside the bag" reads as negated and is
dropped -- the scene gets thinner, the bag still never reaches the model. The
opposite error (an affirmative bag kept) is impossible: a sentence survives
only if it names no packaging at all, or names it outside every negation.
"""

from __future__ import annotations

import re
from typing import Final

from lib.medical_claims_validator import _NEGATION_WORDS

# Nouns that make an image model paint a product surface. `brand text` is not
# listed: a brief saying "no brand text" would then fail its own instruction,
# and the brand half of rule 5 is the token check instead. Everything past
# `boxes` was added 2026-09-18: the prompt's own catch-all is "or any readable
# surface", and a pouch, a tub or a jar is exactly as paintable as a bag.
# Whole-word throughout, on purpose -- `tin` must not fire on "tiny", `tub` not
# on "tube", `jar` not on "jarring".
PACKAGING_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:packaging|packages?|labels?|logos?|bags?|box|boxes|pouch(?:es)?"
    r"|tubs?|canisters?|jars?|packets?|wrappers?|tins?|cartons?|containers?)\b",
    re.IGNORECASE,
)

#: The medical gate's grammatical negations plus the ones a scene description
#: actually uses to say "absent": "Nothing in frame…", "none visible",
#: "free of labels", "zero branding".
_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in (*_NEGATION_WORDS, "nothing", "none", "neither", "zero"))
    + r"|free\s+of)\b",
    re.IGNORECASE,
)

#: Where a negation stops applying. Commas deliberately do NOT end it -- the
#: failing briefs negate a LIST ("text, labels or packaging"). A dash, a colon
#: or a contrastive word starts the part that describes what IS there
#: ("-- just the dog, the hand, the treat").
_SCOPE_END_RE: Final[re.Pattern[str]] = re.compile(
    r"--|[;:–—]|\b(?:but|except|while|though|although|just|only|instead)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n+")


def _negated_spans(sentence: str) -> list[tuple[int, int]]:
    """Each ``[cue, end of its scope)`` span in ``sentence``."""
    spans: list[tuple[int, int]] = []
    for cue in _NEGATION_RE.finditer(sentence.replace("’", "'")):
        end = _SCOPE_END_RE.search(sentence, cue.end())
        spans.append((cue.start(), end.start() if end else len(sentence)))
    return spans


def _only_negated_packaging(sentence: str) -> bool:
    """True when the sentence names packaging and EVERY mention sits inside a
    negation's scope -- i.e. it only ever says the packaging is absent."""
    hits = [m.start() for m in PACKAGING_RE.finditer(sentence)]
    if not hits:
        return False
    spans = _negated_spans(sentence)
    return all(any(start <= hit < end for start, end in spans) for hit in hits)


def scrub_image_brief(brief: str) -> str:
    """``brief`` without the sentences that only say packaging is absent.

    Everything else is returned verbatim, including any sentence that names
    packaging affirmatively -- rule 5 still rejects those.
    """
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(brief.strip()) if s.strip()]
    return " ".join(s for s in sentences if not _only_negated_packaging(s))


__all__ = ["PACKAGING_RE", "scrub_image_brief"]
